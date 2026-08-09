/**
 * The stage rail at both levels of precision, plus the jargon regression
 * check for the default experience.
 *
 * The rule under test: in default mode the rail speaks plain language and
 * carries no engine vocabulary; in debug mode it speaks the engine's own
 * eight stages. Nothing is dropped between the two — the precision moves.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { groupStages, StageRail } from "@/components/chat/StageRail";
import { emptyAnswer, type StageStatus } from "@/lib/store";
import { PLAIN_STAGE_GROUPS, STAGE_ORDER, type StageId } from "@/lib/types";

/**
 * Internal vocabulary that must not reach an analyst in default mode. The
 * design doc names these explicitly; domain words analysts use (denial,
 * CARC, payer, AR) are deliberately absent from this list.
 */
const JARGON = [
  /\bprobes?\b/i,
  /\bwatermark\b/i,
  /\bepoch\b/i,
  /\bplan hash\b/i,
  /\bdiscovery\b/i,
  /\bframe\b/i,
  /\bschema\b/i,
  /\bstructured output\b/i,
  /\bzero-probe\b/i,
];

function stagesWith(overrides: Partial<Record<StageId, StageStatus["state"]>>): StageStatus[] {
  return STAGE_ORDER.map((stage) => ({ stage, state: overrides[stage] ?? "pending" }));
}

afterEach(() => cleanup());

describe("groupStages", () => {
  it("rolls the eight engine stages into the four plain steps", () => {
    const groups = groupStages(emptyAnswer().stages);
    expect(groups.map((g) => g.label)).toEqual(PLAIN_STAGE_GROUPS.map((g) => g.label));
    expect(groups.every((g) => g.state === "pending")).toBe(true);
  });

  it("marks a step done only once every stage inside it has finished", () => {
    const groups = groupStages(stagesWith({ classified: "done" }));
    expect(groups[0].state).toBe("pending"); // interpreted still pending
    expect(groupStages(stagesWith({ classified: "done", interpreted: "done" }))[0].state).toBe(
      "done",
    );
  });

  it("keeps a skipped step visibly skipped rather than quietly done", () => {
    const groups = groupStages(
      stagesWith({ executing: "skipped", calculating: "skipped", reconciled: "skipped" }),
    );
    expect(groups[2].state).toBe("skipped");
  });

  it("reports a step active while any stage inside it is running", () => {
    const groups = groupStages(stagesWith({ planned: "done", validated: "active" }));
    expect(groups[1].state).toBe("active");
  });
});

describe("StageRail — default mode speaks plain language", () => {
  it("labels the steps the way the spec words them", () => {
    render(<StageRail stages={emptyAnswer().stages} streaming cacheHits={0} />);

    expect(screen.getByText("Reading your question")).toBeInTheDocument();
    expect(screen.getByText("Deciding what to check")).toBeInTheDocument();
    expect(screen.getByText("Checking the numbers")).toBeInTheDocument();
    expect(screen.getByText("Writing it up")).toBeInTheDocument();
  });

  it("carries no internal vocabulary while streaming", () => {
    const { container } = render(
      <StageRail
        stages={stagesWith({ classified: "done", interpreted: "done", executing: "active" })}
        streaming
        cacheHits={2}
      />,
    );

    for (const pattern of JARGON) {
      expect(container.textContent ?? "").not.toMatch(pattern);
    }
  });

  it("carries no internal vocabulary in the collapsed summary either", () => {
    const stages = stagesWith({
      classified: "done",
      interpreted: "done",
      planned: "done",
      validated: "done",
      executing: "skipped",
      calculating: "skipped",
      reconciled: "skipped",
      narrating: "done",
    });
    stages[STAGE_ORDER.indexOf("executing")].probesTotal = 3;
    const { container } = render(
      <StageRail stages={stages} streaming={false} cacheHits={2} />,
    );

    for (const pattern of JARGON) {
      expect(container.textContent ?? "").not.toMatch(pattern);
    }
    // The counts survive the rewording — only the words changed.
    expect(screen.getByText(/3 data checks/)).toBeInTheDocument();
    expect(screen.getByText(/2/)).toBeInTheDocument();
    expect(screen.getByText(/some steps weren’t needed/)).toBeInTheDocument();
  });
});

describe("StageRail — debug mode restores the engine's own stages", () => {
  it("names all eight stages", () => {
    render(<StageRail stages={emptyAnswer().stages} streaming cacheHits={0} debug />);

    expect(screen.getByText("classified")).toBeInTheDocument();
    expect(screen.getByText("interpreted")).toBeInTheDocument();
    expect(screen.getByText("validated")).toBeInTheDocument();
    expect(screen.getByText("narrating")).toBeInTheDocument();
    expect(screen.queryByText("Reading your question")).not.toBeInTheDocument();
  });

  it("shows probe progress on the executing stage", () => {
    const stages = stagesWith({ executing: "active" });
    const executing = stages[STAGE_ORDER.indexOf("executing")];
    executing.probesDone = 2;
    executing.probesTotal = 3;

    render(<StageRail stages={stages} streaming cacheHits={0} debug />);

    expect(screen.getByText("probes 2/3")).toBeInTheDocument();
  });

  it("keeps the zero-probe wording in the collapsed technical summary", () => {
    const stages = stagesWith({ executing: "skipped", classified: "done" });
    render(<StageRail stages={stages} streaming={false} cacheHits={0} debug />);

    expect(screen.getByText(/skipped \(zero-probe path\)/)).toBeInTheDocument();
  });
});

/**
 * The rail is the only thing that speaks during a 26–60 second turn, so
 * the step the pipeline is ON announces itself — and only that one. Eight
 * chips all claiming `role="status"` would announce the whole rail on
 * every transition, which is the noise that gets live regions turned off.
 */
describe("StageRail — the running step announces itself", () => {
  it("puts role=status on the active step and nothing else", () => {
    const { container } = render(
      <StageRail
        stages={stagesWith({ classified: "done", interpreted: "done", executing: "active" })}
        streaming
        cacheHits={0}
      />,
    );

    const announced = container.querySelectorAll("[role='status']");
    expect(announced).toHaveLength(1);
    expect(announced[0]?.textContent).toContain("Checking the numbers");
  });

  it("announces nothing once every step is done", () => {
    const { container } = render(
      <StageRail
        stages={STAGE_ORDER.map((stage) => ({ stage, state: "done" as const }))}
        streaming
        cacheHits={0}
      />,
    );

    expect(container.querySelectorAll("[role='status']")).toHaveLength(0);
  });
});
