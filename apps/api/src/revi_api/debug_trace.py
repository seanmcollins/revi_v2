"""Trace record → :class:`DebugTracePayload`, guarded on the way out.

One projection, two doors: the ``debug`` block on a turn response and
``GET /v1/investigations/{id}/trace`` are the same function over the same
stored :class:`TraceRecord`. That is deliberate — a debug view that
recomputed anything could disagree with the answer it claims to explain,
and two views that projected differently would disagree with each other.

**The guard decides what free text leaves.** Every string that originated
outside the platform's own vocabulary (the analyst's question, the
clarification text, plan warnings, refinement rationale) is run through
``assert_safe_payload`` — the same check that stands between a prompt and
a model. A field the guard rejects is replaced with a marker and named in
``redactions``: never emitted, and never quietly dropped either, because
an operator who cannot see that something was withheld will read the gap
as "nothing happened".

Ids, hashes, grades, timings and token counts are platform-generated and
travel unguarded; running a plan hash past a PHI check would be theatre.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from revi_investigation.application.llm.guard import assert_safe_payload
from revi_investigation.application.ports import TraceRecord
from revi_investigation_contracts.debug import (
    DebugInterpretation,
    DebugLlmCall,
    DebugProbe,
    DebugTracePayload,
)
from revi_investigation_contracts.settings import SessionSettingsModel
from revi_kernel.errors import PolicyDeniedError
from revi_kernel.grades import EvidenceGrade, min_grade

#: What stands in for text the guard refused to release.
REDACTED = "[redacted: withheld by the outbound-payload guard]"


class _Guarded:
    """Guard free text, remembering which fields were withheld."""

    def __init__(self) -> None:
        self.redactions: list[str] = []

    def text(self, field: str, value: object) -> str | None:
        if value is None:
            return None
        text = str(value)
        try:
            assert_safe_payload(text)
        except PolicyDeniedError as exc:
            rule = exc.details.get("rule", "sensitive content")
            self.redactions.append(f"{field} ({rule})")
            return REDACTED
        return text

    def texts(self, field: str, values: Sequence[Any]) -> list[str]:
        out: list[str] = []
        for index, value in enumerate(values):
            guarded = self.text(f"{field}[{index}]", value)
            if guarded is not None:
                out.append(guarded)
        return out


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def _settings_payload(raw: Mapping[str, Any]) -> SessionSettingsModel:
    """The settings the turn ran under, as recorded.

    Defaults are used for anything a trace predating a field could not
    have carried — an older trace is not a trace that ran with debug on.
    """
    if not raw:
        return SessionSettingsModel()
    return SessionSettingsModel(
        model_tier=raw.get("model_tier"),
        max_turn_cost_usd=raw.get("max_turn_cost_usd"),
        narrative_depth=raw.get("narrative_depth", "summary"),
        evidence_depth=raw.get("evidence_depth", "standard"),
        debug=bool(raw.get("debug", False)),
    )


def _probe(raw: Mapping[str, Any]) -> DebugProbe:
    rows = raw.get("rows")
    limit = raw.get("limit")
    return DebugProbe(
        id=str(raw.get("id", "")),
        hash=str(raw.get("hash", "")),
        purpose=str(raw.get("purpose", "")),
        kind=str(raw.get("kind", "")),
        metrics=[dict(_mapping(m)) for m in _sequence(raw.get("metrics"))],
        cache_hit=bool(raw.get("cache_hit", False)),
        rows=int(rows) if isinstance(rows, int) else None,
        limit=int(limit) if isinstance(limit, int) else None,
        truncated=bool(raw.get("truncated", False)),
        suppressed_cells=int(raw.get("suppressed_cells", 0) or 0),
        grade=raw.get("grade"),
        duration_ms=int(raw.get("duration_ms", 0) or 0),
    )


def _llm_call(raw: Mapping[str, Any]) -> DebugLlmCall:
    return DebugLlmCall(
        template=str(raw.get("template", "")),
        model=str(raw.get("model", "")),
        input_tokens=int(raw.get("input_tokens", 0) or 0),
        output_tokens=int(raw.get("output_tokens", 0) or 0),
        cost_usd=str(raw.get("cost_usd", "0")),
        schema_retries=int(raw.get("schema_retries", 0) or 0),
        attempts=int(raw.get("attempts", 1) or 1),
        duration_ms=int(raw.get("duration_ms", 0) or 0),
        failure=raw.get("failure"),
    )


def _interpretation(raw: Mapping[str, Any], guard: _Guarded) -> DebugInterpretation | None:
    if not raw:
        return None
    window = _mapping(raw.get("window"))
    return DebugInterpretation(
        # The intent summary is model-written prose about the analyst's
        # question — the one interpretation field that is not an id.
        intent_summary=guard.text("interpretation.intent_summary", raw.get("intent_summary", ""))
        or "",
        metric_ids=[str(x) for x in _sequence(raw.get("metric_ids"))],
        dimension_ids=[str(x) for x in _sequence(raw.get("dimension_ids"))],
        concept_ids=[str(x) for x in _sequence(raw.get("concept_ids"))],
        playbook_id=raw.get("playbook_id"),
        window_start=window.get("start"),
        window_end=window.get("end"),
        basis=window.get("basis"),
    )


def _weakest_grade(grades: Mapping[str, Any]) -> str | None:
    """The grade law applied to the recorded node grades (§5.3).

    Emitted rather than left for a reader to eyeball out of a dict: it is
    the ceiling on what the whole answer may claim, and the number a debug
    reader checks the caveats against.
    """
    values: list[EvidenceGrade] = []
    for raw in grades.values():
        try:
            values.append(EvidenceGrade(str(raw)))
        except ValueError:  # a grade this build does not know; not a crash
            continue
    if not values:
        return None
    return min_grade(*values).value


def build_debug_trace(record: TraceRecord) -> DebugTracePayload:
    """Project one recorded turn into the debug wire shape."""
    payload = record.payload
    guard = _Guarded()
    classification = _mapping(payload.get("classification"))
    refinement = _mapping(payload.get("refinement"))
    plan_context = _mapping(payload.get("plan_context"))
    pack = _mapping(payload.get("pack"))
    watermark = _mapping(payload.get("watermark"))
    epoch = _mapping(payload.get("epoch"))
    grades = {str(k): str(v) for k, v in _mapping(payload.get("grades")).items()}

    confidence = classification.get("confidence")
    return DebugTracePayload(
        trace_id=record.trace_id,
        session_id=record.session_id,
        investigation_id=record.investigation_id,
        turn_id=record.turn_id,
        settings=_settings_payload(_mapping(payload.get("settings"))),
        question=guard.text("question", payload.get("question")),
        turn_class=classification.get("turn_class"),
        classification_confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        interpretation=_interpretation(_mapping(payload.get("interpretation")), guard),
        refinement_operators=[dict(_mapping(op)) for op in _sequence(refinement.get("operators"))],
        refinement_rationale=guard.text("refinement.rationale", refinement.get("rationale")),
        referent_resolutions=[
            dict(_mapping(res)) for res in _sequence(refinement.get("resolutions"))
        ],
        clarification_reason=guard.text(
            "clarification_reason", payload.get("clarification_reason")
        ),
        plan_hash=payload.get("plan_hash"),
        playbook_id=plan_context.get("playbook_id"),
        probes=[_probe(_mapping(p)) for p in _sequence(payload.get("probes"))],
        grades=grades,
        weakest_grade=_weakest_grade(grades),
        finding_grades={
            str(k): str(v) for k, v in _mapping(payload.get("finding_grades")).items()
        },
        calculation_operators=[dict(_mapping(op)) for op in _sequence(payload.get("operators"))],
        # Top level since the verdict is recorded for every analytical
        # turn; the ``refinement`` copy is where it lived before that and
        # is still read for traces written under the old shape.
        reconciliation=payload.get("reconciliation") or refinement.get("reconciliation"),
        warnings=guard.texts("warnings", _sequence(payload.get("warnings"))),
        llm_calls=[_llm_call(_mapping(call)) for call in _sequence(payload.get("llm"))],
        template_hashes={
            str(k): str(v) for k, v in _mapping(payload.get("template_hashes")).items()
        },
        timings_ms={
            str(k): int(v)
            for k, v in _mapping(payload.get("timings_ms")).items()
            if isinstance(v, int)
        },
        watermark_id=str(watermark.get("id", "")),
        watermark_stale=bool(payload.get("watermark_stale", False)),
        epoch=int(epoch.get("index", 0) or 0),
        re_anchored=bool(epoch.get("re_anchored", False)),
        pack_id=str(pack.get("id", "")),
        pack_version=str(pack.get("version", "")),
        pack_snapshot_id=str(pack.get("snapshot_id", "")),
        redactions=guard.redactions,
    )
