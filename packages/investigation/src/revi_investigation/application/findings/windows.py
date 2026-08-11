"""What window a probe actually measured, and how an answer says so."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from revi_investigation.application.capability_ports import PackPort
from revi_investigation.application.comparison import (
    comparison_range_for,
)
from revi_investigation.domain.context import (
    AnalysisSpec,
)
from revi_investigation.domain.records import Finding
from revi_kernel.filters import Scalar
from revi_kernel.frame import EvidenceFrame
from revi_kernel.scope import AbsoluteRange, TimeWindow

_TIME_BUCKET_PREFIX = "time_bucket:"


#: The adapter's ratio-denominator column suffix (``denial_rate__den``).
#: Read here to judge whether a series' terminal bucket has settled.
_DENOMINATOR_SUFFIX = "__den"


#: Node-id prefix of the premise-verification probe (see
#: ``BuildInvestigationPlanService.PREMISE_PREFIX``). Compared as a string
#: so this module keeps its existing import surface.
_PREMISE_PREFIX = "premise"


#: The contract ``kind`` that reports a balance standing at a moment rather
#: than a quantity accumulated over a window. Compared as a string so this
#: module keeps its existing import surface.
_SNAPSHOT_KIND = "snapshot"


def _is_snapshot(pack: PackPort, measure: str) -> bool:
    contract = pack.metric(measure)
    return contract is not None and str(contract.kind) == _SNAPSHOT_KIND


def _measured_range(spec: AnalysisSpec, window: TimeWindow | None) -> AbsoluteRange:
    """The range a published figure was actually computed over.

    ``window`` is the probe's own resolved window when it declared one (see
    :func:`~revi_investigation.application.planning.frame_window`); ``None``
    means the probe read the investigation window, which is the ordinary
    case and the only one before playbooks with their own probe windows.
    """
    return (window or spec.context.window).range


#: A date as a reader says it. ISO ranges belong in Evidence
#: (``docs/client-language.md`` §4), not on a default surface.
_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _readable(value: date) -> str:
    return f"{_MONTHS[value.month - 1]} {value.day}, {value.year}"


def probe_window_disclosure(spec: AnalysisSpec, window: TimeWindow | None) -> str | None:
    """Why this finding's period is not the one in the context header.

    ``None`` when the probe read the investigation window — the ordinary
    case, and one that must stay silent: a sentence explaining that a
    number was computed over the window the header names would be noise on
    every answer this engine gives.
    """
    if window is None or window.range == spec.context.window.range:
        return None
    header = spec.context.window.range
    own = window.range
    return (
        f"This check runs on its own period ({_readable(own.start)} to {_readable(own.end)}), "
        f"not the answer's ({_readable(header.start)} to {_readable(header.end)}). The period "
        "this measure is read over comes with the measure, and the figure above is stated "
        "over the period it was computed on."
    )


def _window_values(
    measure: str, spec: AnalysisSpec, window: TimeWindow | None
) -> list[tuple[str, Scalar]]:
    """The period this figure was computed over, as NAMED VALUES.

    The same move ``_bound_values`` makes, for the same reason: prose is
    not a contract. A card, a CSV, a restored header and an independent
    re-derivation all need to ask "which period is this number over?" and
    get one answer, and parsing it back out of a sentence is not asking.

    Empty when the probe read the investigation window — which the context
    header already publishes, and which is every finding on every answer
    that carries no playbook probe window of its own. Two names rather than
    one range object because every other value on a finding is a scalar,
    and a consumer that can read ``denial_rate__bound_population`` can read
    these without learning a second shape.
    """
    if window is None or window.range == spec.context.window.range:
        return []
    out: list[tuple[str, Scalar]] = [
        (f"{measure}{WINDOW_START_SUFFIX}", window.range.start),
        (f"{measure}{WINDOW_END_SUFFIX}", window.range.end),
    ]
    comparison = spec.context.comparison
    if comparison is not None:
        prior = comparison_range_for(comparison, window, spec.context.window)
        if prior != comparison.window.range:
            out.extend(
                [
                    (f"{measure}{PRIOR_WINDOW_START_SUFFIX}", prior.start),
                    (f"{measure}{PRIOR_WINDOW_END_SUFFIX}", prior.end),
                ]
            )
    return out


#: Suffixes of the named values above, so a consumer can read them back.
WINDOW_START_SUFFIX = "__window_start"


WINDOW_END_SUFFIX = "__window_end"


#: …and the range the comparison on this finding was taken against, when the
#: probe's own window moved it. The planner pairs a probe with a prior twin
#: derived from the PROBE's window, so a six-month probe under a one-month
#: question is differenced against the six months before it — which the
#: comparison phrase now names, and which this publishes as data.
PRIOR_WINDOW_START_SUFFIX = "__prior_window_start"


PRIOR_WINDOW_END_SUFFIX = "__prior_window_end"


def published_window_note(findings: Sequence[Finding]) -> str | None:
    """What the context header owes a reader, read off the FINDINGS.

    A playbook probe template may declare its own window, which the planner
    resolves and applies whenever the analyst named none of their own
    (``daily_portfolio``'s denial-rate probe reads ``{4, week,
    full_periods}``). Every figure it produced is correct over THAT period
    while the header names the investigation window, so without this note the
    answer publishes one period over numbers computed across another.

    Composed from the published findings rather than from the plan for two
    reasons. It names only periods a reader can actually see a number
    over — a probe that published nothing is not something to warn about —
    and it is the identical computation on a live turn and on a RESTORED
    one, which holds a ``plan_hash`` rather than a plan. Both read the same
    named values off the same findings and produce the same sentence.

    ``None`` when every finding was measured over the investigation window,
    which is every answer that runs no playbook probe window of its own.
    Nothing is re-scoped to make this go away: the window the probe read is
    the window the pack authored.
    """
    ranges: set[tuple[date, date]] = set()
    for finding in findings:
        starts: dict[str, date] = {}
        ends: dict[str, date] = {}
        for name, value in finding.values:
            if name.endswith(WINDOW_START_SUFFIX) and isinstance(value, date):
                starts[name[: -len(WINDOW_START_SUFFIX)]] = value
            elif name.endswith(WINDOW_END_SUFFIX) and isinstance(value, date):
                ends[name[: -len(WINDOW_END_SUFFIX)]] = value
        for measure, start in starts.items():
            end = ends.get(measure)
            if end is not None:
                ranges.add((start, end))
    if not ranges:
        return None
    text = "; ".join(
        f"{_readable(start)} to {_readable(end)}" for start, end in sorted(ranges)
    )
    return (
        "some checks here use their own periods, which come with the measure rather than "
        f"from your question ({text}) — each result states the period it was computed over"
    )


def _with_window_note(statement: str, spec: AnalysisSpec, window: TimeWindow | None) -> str:
    """Append the probe's own-window disclosure, when there is one to make."""
    note = probe_window_disclosure(spec, window)
    return f"{statement} {note}" if note else statement


def _period_phrase(
    spec: AnalysisSpec,
    pack: PackPort,
    measure: str,
    frame: EvidenceFrame,
    window: TimeWindow | None = None,
) -> str:
    """"over 2026-07-01..2026-07-31" — or "as of 2026-08-02" for a snapshot.

    A ``kind: snapshot`` contract reads the balance at the watermark and
    applies no start..end predicate, so stamping the turn's window on its
    title claims a scoping that did not happen — ``timely filing at risk
    dollars: $22,426,000.28 (2026-07-01..2026-07-31)`` over an ALL-TIME
    total whose July figure is $5,565,290.35. The window is not removed from
    the answer (the cohort and charts are scoped by it); it is removed from
    the sentence that says what the number measures.

    ``window`` is the same rule applied to the other axis. A playbook probe
    may declare its own window, which the planner resolves and applies; the
    period said here is then the PROBE's, because a figure computed over
    2026-07-06..2026-08-02 and titled ``(2026-07-01..2026-07-31)`` states
    the same untrue scoping.
    """
    if _is_snapshot(pack, measure):
        return f"as of {frame.watermark.newest_data_date.isoformat()}"
    measured = _measured_range(spec, window)
    return f"over {measured.start.isoformat()}..{measured.end.isoformat()}"


def _period_paren(
    spec: AnalysisSpec,
    pack: PackPort,
    measure: str,
    frame: EvidenceFrame,
    window: TimeWindow | None = None,
) -> str:
    """The same period, parenthesized for a title."""
    if _is_snapshot(pack, measure):
        return f"(as of {frame.watermark.newest_data_date.isoformat()})"
    measured = _measured_range(spec, window)
    return f"({measured.start.isoformat()}..{measured.end.isoformat()})"


_PRIOR_SUFFIX = "__prior"
