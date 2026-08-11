/**
 * WHAT "COPY LINK" PROMISES.
 *
 * The demo sequence that produced this: the operator answers the CFO's
 * question, presses Copy link, the CFO opens it, and the paragraph
 * everyone just read is replaced by a sentence saying it was not kept. The
 * page was honest; the button was silent.
 *
 * Every assertion here is about the same property — the disclosure states
 * what this browser has SEEN come back, never what this build believes the
 * deployment stores — because the deployment is being changed underneath
 * it and a hardcoded "the analysis isn't stored" would be a lie the
 * morning it starts being stored.
 */

import { describe, expect, it } from "vitest";

import { sessionLinkDisclosure, type ShareTurnFacts } from "@/lib/shareDisclosure";

function turn(over: Partial<ShareTurnFacts> = {}): ShareTurnFacts {
  return {
    rehydrated: false,
    narrative: "Denial rate rose 3.6 points on State Medicaid MCO.",
    findings: 3,
    charts: 1,
    hasEvidence: true,
    ...over,
  };
}

const all = (d: { included: string[]; omitted: string[] }) =>
  [...d.included, ...d.omitted].join(" ");

describe("the link discloses what it carries before it is copied", () => {
  it("always says the link is to the session, not to a snapshot", () => {
    for (const turns of [[], [turn()], [turn({ rehydrated: true })]]) {
      expect(sessionLinkDisclosure(turns).lead).toMatch(
        /link to the session, not a snapshot/,
      );
    }
  });

  it("never claims stage timings survive — nothing stores them", () => {
    expect(all(sessionLinkDisclosure([turn()]))).toMatch(/stage timings/i);
  });
});

describe("what it says depends on what came back, not on what this build assumes", () => {
  it("says the analysis isn't stored when the restore came back without it", () => {
    const d = sessionLinkDisclosure([
      turn({ rehydrated: true, narrative: "" }),
      turn({ rehydrated: true, narrative: "   " }),
    ]);
    expect(d.basis).toBe("observed");
    expect(d.omitted.join(" ")).toMatch(
      /written analysis isn't stored — the link opens with the findings, charts and evidence/,
    );
    expect(d.included.join(" ")).not.toMatch(/written analysis/);
  });

  it("says the analysis IS carried the moment a restore comes back with it", () => {
    // The other side of the same switch, so the backend lane's fix does
    // not need a second edit here to become true on the button.
    const d = sessionLinkDisclosure([turn({ rehydrated: true })]);
    expect(d.basis).toBe("observed");
    expect(d.included.join(" ")).toMatch(/The written analysis, as it was composed/);
    expect(d.omitted.join(" ")).not.toMatch(/isn't stored/);
  });

  it("says 'on some answers' when the session restored both kinds", () => {
    const d = sessionLinkDisclosure([
      turn({ rehydrated: true }),
      turn({ rehydrated: true, narrative: "" }),
    ]);
    expect(d.omitted.join(" ")).toMatch(/on some answers/);
  });

  it("names charts and evidence only when it has watched them restore", () => {
    const bare = sessionLinkDisclosure([
      turn({ rehydrated: true, narrative: "", charts: 0, hasEvidence: false }),
    ]);
    // A turn can simply have had no chart. Absence is not evidence that
    // charts are dropped, so nothing is claimed either way — which is the
    // mirror of the server note that claims charts while shipping [].
    expect(bare.included.join(" ")).not.toMatch(/charts/);
    const rich = sessionLinkDisclosure([turn({ rehydrated: true, narrative: "" })]);
    expect(rich.included.join(" ")).toMatch(/charts, rebuilt from what/);
    expect(rich.included.join(" ")).toMatch(/evidence bundle/);
  });
});

describe("a session it has not re-read says so rather than guessing", () => {
  it("marks the basis unobserved when every turn was watched live", () => {
    // THE DEMO CASE, and the one the round-9 pass got wrong: the reviewer
    // re-read their answer in the tab that created it, where the client
    // store still held the prose, and recorded that answers restore with
    // their narrative. They do not.
    const d = sessionLinkDisclosure([turn(), turn()]);
    expect(d.basis).toBe("unobserved");
    expect(d.omitted.join(" ")).toMatch(/has been re-read from the server yet/);
    // It does not assert the analysis is missing — it names it as the
    // thing to check and hands over the one-step check.
    expect(d.omitted.join(" ")).toMatch(/open the link once yourself/);
  });

  it("treats an empty session the same way", () => {
    expect(sessionLinkDisclosure([]).basis).toBe("unobserved");
  });
});
