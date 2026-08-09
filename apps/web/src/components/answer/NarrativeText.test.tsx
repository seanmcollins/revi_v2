/**
 * Referent citations in a live narrative.
 *
 * The server's narrative validator is `\b[FD]\d+\b`
 * (`revi_presentation.narrative`) — it REDACTS any sentence that states
 * figures without a bare referent, so every sentence that survives to the
 * client cites that way. This component matched only the bracketed `[F2]`
 * form, which is the hand-written fixture's spelling, so on a live answer
 * not one citation ever became a chip.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { NarrativeText } from "@/components/answer/NarrativeText";

afterEach(cleanup);

describe("NarrativeText — referent citations", () => {
  it("turns a BARE referent into a chip (the spelling the server emits)", () => {
    render(<NarrativeText text="Cash fell 12.7% week over week, concentrated in F2." />);
    expect(screen.getByRole("button", { name: "F2" })).toBeInTheDocument();
  });

  it("still turns the bracketed form into a chip", () => {
    render(<NarrativeText text="Cash fell, concentrated in [F2]." />);
    expect(screen.getByRole("button", { name: "F2" })).toBeInTheDocument();
  });

  it("chips dimension referents too — the validator accepts D as well as F", () => {
    render(<NarrativeText text="State Medicaid (D9) leads the decline." />);
    expect(screen.getByRole("button", { name: "D9" })).toBeInTheDocument();
  });

  it("leaves ordinary prose alone", () => {
    const { container } = render(
      <NarrativeText text="Denials rose on Atlas Health across CARC 16 and PR 3." />,
    );
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });
});
