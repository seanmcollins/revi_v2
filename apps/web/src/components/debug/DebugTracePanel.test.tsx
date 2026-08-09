/**
 * Debug mode's decision trace, rendered from a fixture shaped exactly like
 * a live `DebugTracePayload` (captured from a running API: probe ids and
 * 64-char content hashes, per-call token/cost fields, stage timings).
 *
 * What these tests hold: every recorded fact reaches the screen, and the
 * ones that would be misread if defaulted — a probe planned but never
 * executed, a model call that came back unusable — are shown as what they
 * are rather than as a zero.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DebugTracePanel } from "@/components/debug/DebugTracePanel";
import { mapDebugTrace } from "@/lib/contract";
import { emptyAnswer, useSessionStore, type AnswerState } from "@/lib/store";

/** The server's own spelling — snake_case, straight off the wire. */
const TRACE_WIRE = {
  trace_id: "trace_9f2c",
  session_id: "sess_75ea4d34e015",
  investigation_id: "inv_1",
  turn_id: "turn_1",
  settings: {
    model_tier: null,
    max_turn_cost_usd: "0.25",
    narrative_depth: "analyst",
    evidence_depth: "deep",
    debug: true,
  },
  question: "Why did cash decline last week?",
  turn_class: "new_investigation",
  classification_confidence: 0.94,
  interpretation: {
    intent_summary: "Weekly cash decline, decomposed by payer",
    metric_ids: ["cash_posted"],
    dimension_ids: ["payer"],
    concept_ids: ["cash"],
    playbook_id: "cash_decline@1",
    window_start: "2026-07-27",
    window_end: "2026-08-02",
    basis: "post",
  },
  refinement_operators: [{ op: "drill_into", target: "F2" }],
  refinement_rationale: "The analyst named F2 explicitly.",
  referent_resolutions: [{ referent: "F2", resolved_to: "inv_0" }],
  plan_hash: "07d31fd003b71685e12ee59ba4f803aea93424cec013db785868079fb81584d5",
  playbook_id: "cash_decline@1",
  probes: [
    {
      id: "cash_by_payer",
      hash: "8094abaefe6462bb08d2fc6c9eb3c34a17ea401904908cc8243563fcc14079bf",
      purpose: "Decline week versus prior week by payer",
      cache_hit: false,
      rows: 12,
      limit: 48,
      truncated: false,
      suppressed_cells: 0,
      grade: "direct",
      duration_ms: 28,
    },
    {
      id: "submission_volume_by_payer",
      hash: "b14c108a2db748e68b227b2b2d867c2e003e98504ea66a1c2b2945c5786f0d63",
      purpose: "Weekly submitted volume by payer",
      cache_hit: true,
      // Planned, never executed — a zero here would read as "no rows".
      rows: null,
      limit: null,
      truncated: true,
      suppressed_cells: 3,
      grade: "derived",
      duration_ms: 0,
    },
  ],
  grades: { cash_by_payer: "direct", narrative: "derived" },
  weakest_grade: "derived",
  finding_grades: { F1: "direct", F2: "derived" },
  calculation_operators: [{ name: "delta", version: "1" }],
  reconciliation: "status=passed; 12 payer rows sum to the parent delta",
  warnings: ["EVIDENCE_TRUNCATED: probe 'submission_volume_by_payer' truncated to the top 48"],
  llm_calls: [
    {
      template: "classify_turn",
      model: "claude-opus-5",
      input_tokens: 1420,
      output_tokens: 96,
      cost_usd: "0.0182",
      schema_retries: 0,
      attempts: 1,
      duration_ms: 1180,
      failure: null,
    },
    {
      template: "interpret_question",
      model: "claude-opus-5",
      input_tokens: 2210,
      output_tokens: 180,
      cost_usd: "0.0311",
      schema_retries: 2,
      attempts: 3,
      duration_ms: 4300,
      failure: "schema",
    },
  ],
  template_hashes: { classify_turn: "a1b2c3" },
  timings_ms: { classify: 1180, plan: 42, execute: 130 },
  watermark_id: "wm_003",
  watermark_stale: false,
  epoch: 1,
  re_anchored: false,
  pack_id: "base-rcm",
  pack_version: "1.0.0",
  pack_snapshot_id: "3da19a0f",
  redactions: ["narrative_prompt"],
};

function answerWithTrace(): AnswerState {
  const trace = mapDebugTrace(TRACE_WIRE);
  if (!trace) throw new Error("fixture failed to map — the mapper and the wire disagree");
  return { ...emptyAnswer(), status: "complete", investigationId: "inv_1", debug: trace };
}

function renderExpanded(answer: AnswerState = answerWithTrace()) {
  render(<DebugTracePanel turnId="turn_1" answer={answer} />);
  fireEvent.click(screen.getByRole("button", { name: /Decision trace/ }));
}

afterEach(() => {
  cleanup();
  useSessionStore.getState().reset();
});

describe("DebugTracePanel — the summary line", () => {
  it("summarises class, confidence, probe count and model spend before expanding", () => {
    render(<DebugTracePanel turnId="turn_1" answer={answerWithTrace()} />);

    expect(
      screen.getByText(/new_investigation \(0\.94\) · 2 probes · 2 llm calls · \$0\.0493/),
    ).toBeInTheDocument();
  });
});

describe("DebugTracePanel — the breakdown", () => {
  it("shows classification and the ids interpretation chose", () => {
    renderExpanded();

    expect(screen.getByText("0.940")).toBeInTheDocument();
    expect(screen.getByText("cash_posted")).toBeInTheDocument();
    // Once under Interpretation, once under Plan — both are recorded.
    expect(screen.getAllByText("cash_decline@1")).toHaveLength(2);
    expect(screen.getByText("2026-07-27 → 2026-08-02 (post basis)")).toBeInTheDocument();
  });

  it("shows the typed refinement operators as recorded", () => {
    renderExpanded();

    expect(screen.getByText('{"op":"drill_into","target":"F2"}')).toBeInTheDocument();
    expect(screen.getByText("The analyst named F2 explicitly.")).toBeInTheDocument();
  });

  it("shows the plan hash and every §6.6 validation warning", () => {
    renderExpanded();

    expect(
      screen.getByText(
        "07d31fd003b71685e12ee59ba4f803aea93424cec013db785868079fb81584d5",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/EVIDENCE_TRUNCATED/)).toBeInTheDocument();
  });

  it("distinguishes a probe that was planned but never executed from an empty one", () => {
    renderExpanded();

    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("not executed")).toBeInTheDocument();
    expect(screen.getByText(/cache · truncated · 3 suppressed/)).toBeInTheDocument();
  });

  it("shows each model call's tokens, cost, retries and failure kind", () => {
    renderExpanded();

    expect(screen.getByText("classify_turn")).toBeInTheDocument();
    expect(screen.getByText("$0.0311")).toBeInTheDocument();
    // The failure kind is the recovery signal — never flattened to "error".
    expect(screen.getByText("schema")).toBeInTheDocument();
  });

  it("shows grade derivation and provenance, including what was withheld", () => {
    renderExpanded();

    expect(screen.getByText("cash_by_payer → direct")).toBeInTheDocument();
    expect(screen.getByText("F2 → derived")).toBeInTheDocument();
    expect(screen.getByText("wm_003")).toBeInTheDocument();
    expect(screen.getByText("base-rcm@1.0.0 · 3da19a0f")).toBeInTheDocument();
    expect(screen.getByText("narrative_prompt")).toBeInTheDocument();
  });

  it("names the settings the turn actually ran under", () => {
    renderExpanded();

    expect(
      screen.getByText(
        "model_tier=pin · max_turn_cost_usd=0.25 · narrative=analyst · evidence=deep",
      ),
    ).toBeInTheDocument();
  });
});

describe("DebugTracePanel — turns answered before debug was on", () => {
  it("offers to read the recorded trace instead of showing nothing", async () => {
    const loadTrace = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ loadTrace });
    const answer: AnswerState = {
      ...emptyAnswer(),
      status: "complete",
      investigationId: "inv_1",
    };

    render(<DebugTracePanel turnId="turn_1" answer={answer} />);
    fireEvent.click(screen.getByRole("button", { name: /Load decision trace/ }));

    expect(loadTrace).toHaveBeenCalledWith("turn_1");
  });

  it("renders nothing at all for a turn with no server id to read", () => {
    const { container } = render(
      <DebugTracePanel turnId="turn_1" answer={{ ...emptyAnswer(), status: "complete" }} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("shows the server's refusal when the trace could not be read", () => {
    const answer: AnswerState = {
      ...emptyAnswer(),
      status: "complete",
      investigationId: "inv_1",
      traceFetch: "error",
      traceError: "debug traces are disabled on this deployment (REVI_DEBUG_TRACE=0)",
    };

    render(<DebugTracePanel turnId="turn_1" answer={answer} />);

    expect(
      screen.getByText("debug traces are disabled on this deployment (REVI_DEBUG_TRACE=0)"),
    ).toBeInTheDocument();
  });
});
