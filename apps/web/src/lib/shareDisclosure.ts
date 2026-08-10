/**
 * WHAT THE LINK WILL CONTAIN — said before it is copied, and derived
 * rather than promised.
 *
 * "Copy link" is the last gesture of every demo: the operator answers the
 * CFO's question, presses it, and the CFO opens the page. What opens is
 * not what was on screen. `/s/{id}` re-joins the session and rebuilds it
 * from what the SERVER kept, and what the server keeps is not everything
 * a live turn published — measured live, no turn persisted its composed
 * narrative, so a shared answer restored as findings plus a sentence
 * saying the analysis was not kept. The product was honest about it on
 * the page it produced and silent about it on the button that produced
 * the page.
 *
 * This module is the disclosure, and it is written to survive the fix as
 * well as the defect. It asserts NOTHING about what the deployment
 * persists. It reports what this browser has actually seen come back from
 * the server for this session's own turns, and when it has seen nothing
 * it says that instead of guessing — because the guess is the part that
 * would go stale the morning the narrative starts persisting.
 */

/** One turn, reduced to the facts a disclosure can be built from. */
export interface ShareTurnFacts {
  /** Rebuilt from server state rather than watched as it streamed. */
  rehydrated: boolean;
  /** The composed write-up, as it came back. Empty means it did not. */
  narrative: string;
  findings: number;
  charts: number;
  hasEvidence: boolean;
}

export interface LinkDisclosure {
  /** What the link IS. True of `/s/{id}` on every deployment. */
  lead: string;
  /** What opens with it. */
  included: string[];
  /** What does not. Never empty — stage timings are never stored. */
  omitted: string[];
  /**
   * Whether the two lists are a MEASUREMENT or a description.
   *
   * `observed` — this browser has re-read at least one turn of this
   *   session from the server, so the lists say what actually came back.
   * `unobserved` — every turn here was watched live and the client store
   *   still holds prose the server may or may not have kept. Reading a
   *   restored turn in this state is what produces the false conclusion
   *   that answers come back with their narrative; they do not.
   */
  basis: "observed" | "unobserved";
}

const LEAD =
  "Opens this session and rebuilds it from what the server kept. It is a link to the session, not a snapshot — a turn asked after you send it will be there too.";

/** Never stored, on any deployment, by design. */
const STAGE_TIMINGS = "The stage timings — those are never stored.";

export function sessionLinkDisclosure(
  turns: readonly ShareTurnFacts[],
): LinkDisclosure {
  const restored = turns.filter((turn) => turn.rehydrated);

  if (restored.length === 0) {
    return {
      lead: LEAD,
      included: [
        "Each turn's findings, its charts and its evidence bundle, and the window, scope, cohort and data load it was measured under.",
      ],
      omitted: [
        STAGE_TIMINGS,
        // The honest shape of an unobserved claim: name the thing most
        // likely to be missing, and hand over the one-step check rather
        // than a reassurance this browser cannot back.
        "Anything else the server did not keep — the written analysis is the usual one. No turn here has been re-read from the server yet, so open the link once yourself before you send it.",
      ],
      basis: "unobserved",
    };
  }

  const proseKept = restored.every((turn) => turn.narrative.trim() !== "");
  const proseLost = restored.every((turn) => turn.narrative.trim() === "");
  const chartsKept = restored.some((turn) => turn.charts > 0);
  const evidenceKept = restored.some((turn) => turn.hasEvidence);

  const included = [
    "Each turn's findings, and the window, scope, cohort and data load it was measured under.",
  ];
  // Absence of a chart is not evidence charts are dropped — a turn can
  // simply have had none — so charts and evidence are named only when
  // this browser has watched them come back.
  if (chartsKept) included.push("Its charts, rebuilt from the frames the server stored.");
  if (evidenceKept) included.push("Its evidence bundle, projected from the recorded trace.");
  if (proseKept) included.push("The written analysis, as it was composed.");

  const omitted = [STAGE_TIMINGS];
  if (proseLost) {
    omitted.push(
      "The written analysis isn't stored — the link opens with the findings, charts and evidence the server kept, and each turn says so where the prose would be.",
    );
  } else if (!proseKept) {
    omitted.push(
      "The written analysis on some turns — this session already has turns that came back without it.",
    );
  }

  return { lead: LEAD, included, omitted, basis: "observed" };
}
