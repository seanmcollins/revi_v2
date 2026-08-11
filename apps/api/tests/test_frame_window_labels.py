"""A chart's period axis names the window ITS OWN frame was measured over.

The live defect: ``denial_spike`` declares its trend probe's window as
2026-06-08..2026-08-02 (against 2026-04-13..2026-06-07) while the turn's
own window is July. Every frame was handed the turn's window, so the
comparison drew two ticks reading ``Jun 2026`` and ``Jul 2026`` over
eight-week spans — and the bar labelled July carried 9.13% while July's
true denial rate is 12.76%. A reader who asked both questions got two
numbers for one month name with nothing on either chart to reconcile them.

Pinned here on both halves of the seam: the plan resolves a window per
frame (:func:`resolved_frame_windows`), and the assembly stage turns that
into the per-frame labels the chart builder draws
(:func:`revi_api.assembly.chart_windows`).
"""

from __future__ import annotations

from datetime import date, datetime

from revi_api.assembly import chart_windows, frame_windows_from_trace
from revi_investigation.application.ports import TraceRecord
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import MetricRef
from revi_kernel.scope import AbsoluteRange
from revi_kernel.watermark import DataWatermark
from revi_presentation import build_chart_specs
from revi_presentation.charts import CURRENT_SERIES, DEFAULT_WINDOW_KEY, PRIOR_SERIES

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)

#: The playbook's own spans, and the turn's. Exactly the live pair.
PLAYBOOK_WINDOW = AbsoluteRange(start=date(2026, 6, 8), end=date(2026, 8, 2))
PLAYBOOK_PRIOR = AbsoluteRange(start=date(2026, 4, 13), end=date(2026, 6, 7))

DENIAL_RATE_TREND = 0.091316
DENIAL_RATE_PRIOR = 0.073576


class _Header:
    """The four dates a turn's context header carries — July, as asked."""

    window_start = date(2026, 7, 1)
    window_end = date(2026, 7, 31)
    comparison_start = date(2026, 6, 1)
    comparison_end = date(2026, 6, 30)


def _compare_frame() -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(
            (
                FrameColumn("denial_rate", MetricRef("denial_rate"), 1, "rate"),
                FrameColumn("denial_rate__prior", MetricRef("denial_rate"), 1, "rate"),
            )
        ),
        rows=((DENIAL_RATE_TREND, DENIAL_RATE_PRIOR),),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


class TestAFrameIsChartedAgainstItsOwnPeriod:
    def test_a_playbook_window_frame_never_inherits_the_headers_month(self) -> None:
        windows = chart_windows(
            _Header(), (("trend__compare", PLAYBOOK_WINDOW, PLAYBOOK_PRIOR),)
        )

        (spec,) = build_chart_specs(
            (("trend__compare", _compare_frame()),), windows=windows
        )

        drawn = [(row.x, row.series, row.value) for row in spec.rows]
        assert drawn == [
            ("Apr 13 — Jun 7, 2026", PRIOR_SERIES, DENIAL_RATE_PRIOR),
            ("Jun 8 — Aug 2, 2026", CURRENT_SERIES, DENIAL_RATE_TREND),
        ]
        # The defect, stated as the assertion that would have caught it: a
        # month name over a span no month covers.
        assert not any(row.x in ("Jul 2026", "Jun 2026") for row in spec.rows)

    def test_the_turns_own_window_stays_the_default_for_undeclared_frames(self) -> None:
        windows = chart_windows(_Header(), ())

        assert windows[DEFAULT_WINDOW_KEY].current_label == "Jul 2026"
        assert windows[DEFAULT_WINDOW_KEY].prior_label == "Jun 2026"

    def test_a_frame_on_the_turns_window_may_inherit_its_baseline_label(self) -> None:
        """The header's comparison is only borrowed where the CURRENT sides
        already agree — otherwise a baseline tick is mislabelled exactly as
        the current one was, one column to the left."""
        own = AbsoluteRange(start=date(2026, 7, 1), end=date(2026, 7, 31))
        windows = chart_windows(_Header(), (("main__compare", own, None),))

        assert windows["main__compare"].current_label == "Jul 2026"
        assert windows["main__compare"].prior_label == "Jun 2026"

    def test_a_playbook_window_frame_with_no_baseline_borrows_none(self) -> None:
        windows = chart_windows(_Header(), (("trend", PLAYBOOK_WINDOW, None),))

        assert windows["trend"].current_label == "Jun 8 — Aug 2, 2026"
        assert windows["trend"].prior_label is None


class TestARestoredTurnLabelsTheSameWay:
    def test_frame_windows_survive_the_trace_round_trip(self) -> None:
        trace = TraceRecord(
            trace_id="t",
            session_id="s",
            investigation_id="i",
            turn_id="turn",
            created_at=datetime(2026, 8, 3, 4, 10),
            payload={
                "plan_context": {
                    "frame_windows": [
                        {
                            "frame_id": "trend__compare",
                            "start": "2026-06-08",
                            "end": "2026-08-02",
                            "prior_start": "2026-04-13",
                            "prior_end": "2026-06-07",
                        }
                    ]
                }
            },
        )

        assert frame_windows_from_trace(trace) == (
            ("trend__compare", PLAYBOOK_WINDOW, PLAYBOOK_PRIOR),
        )

    def test_an_unparseable_entry_is_dropped_rather_than_defaulted(self) -> None:
        trace = TraceRecord(
            trace_id="t",
            session_id="s",
            investigation_id="i",
            turn_id="turn",
            created_at=datetime(2026, 8, 3, 4, 10),
            payload={
                "plan_context": {
                    "frame_windows": [{"frame_id": "trend", "start": "nope", "end": "also"}]
                }
            },
        )

        assert frame_windows_from_trace(trace) == ()
