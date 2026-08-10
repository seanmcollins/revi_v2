/**
 * DEFINITIONAL-turn fixture: "what is PR3" resolved against governed pack
 * content (alias-normalize → group code PR + CARC 3), zero probes.
 * CARC text is paraphrased — the licensed X12 list is never redistributed.
 */

import type { TurnEvent } from "@/lib/types";
import { PACK, WATERMARK } from "@/lib/mock/reference";

export const PR3_EVENTS: TurnEvent[] = [
  { type: "stage", stage: "classified", status: "completed", detail: "DEFINITIONAL · 0.99" },
  {
    type: "interpretation",
    interpretation: {
      metric: { id: "—", name: "no metric — knowledge lookup", version: 0 },
      windowDescription: "Not applicable — this is a definition, not a measurement",
      filterDescriptions: [],
      synonymMappings: [{ from: "PR3", to: "group code PR + CARC 3", note: "alias-normalized" }],
      planDiff: ["No data check ran — answered from governed pack content"],
      appliedOperators: [],
    },
  },
  { type: "stage", stage: "interpreted", status: "completed", detail: "PR3 → group PR + CARC 3" },
  {
    type: "context_header",
    header: {
      window: { start: "2026-07-27", end: "2026-08-02", basis: "post", requested: "n/a" },
      filters: [],
      grain: { entity: "claim" },
      watermark: WATERMARK,
      packVersion: PACK,
    },
    turnClass: "definitional",
  },
  { type: "stage", stage: "planned", status: "skipped", detail: "zero-probe path" },
  { type: "stage", stage: "validated", status: "skipped" },
  { type: "stage", stage: "executing", status: "skipped", probesDone: 0, probesTotal: 0 },
  { type: "stage", stage: "calculating", status: "skipped" },
  { type: "stage", stage: "reconciled", status: "skipped" },
  {
    type: "definition_card",
    definition: {
      term: "PR3",
      normalizedTo: "Group code PR · CARC 3",
      definition:
        "An adjustment stating that the amount was applied as the member's copay. The PR group means the balance moves to the patient — it is patient responsibility, not a payer write-off, and it belongs in point-of-service or patient collections, never in denial write-offs.",
      groupCode: {
        code: "PR",
        meaning:
          "Patient Responsibility — the adjusted amount is owed by the member (copay, coinsurance, deductible).",
      },
      carc: {
        code: 3,
        paraphrase: "Amount was applied as the member's copay.",
        category: "PATIENT_RESP",
      },
      sources: [
        { label: "base-rcm pack · knowledge/group_codes.yaml", authority: "governed_pack" },
        { label: "X12 CARC 3 — paraphrased (original text is licensed)", authority: "standard_paraphrase" },
        { label: "Concept dictionary: copay · patient responsibility", authority: "concept_dictionary" },
      ],
      packVersion: PACK,
      relatedConcepts: ["Copay", "Patient responsibility", "POS collections", "PR group", "Coinsurance (PR2)", "Deductible (PR1)"],
    },
  },
  { type: "stage", stage: "narrating", status: "started" },
  {
    type: "narrative_delta",
    text:
      "PR3 is a claim-adjustment pair: group code PR (patient responsibility) with CARC 3 — the amount was applied as the member's copay. Because the group is PR, the balance transfers to the patient rather than being written off against the payer; in denial analytics PR-group adjustments are tracked as patient responsibility, not payer denials. Answered from governed pack content (base-rcm@1.0.0) — zero warehouse queries.",
  },
  {
    type: "evidence",
    evidence: {
      // A definitional turn computes no numbers: no probes, and no
      // reconciliation verdict is ever reached — the same absence the
      // live engine records for this path.
      zeroProbeTurn: true,
      warehouseQueries: 0,
      cacheHits: 0,
      probes: [],
    },
  },
  {
    type: "turn_complete",
    investigationId: "inv_def_pr3",
    status: "complete",
  },
];

/** Fallback: unscripted input → first-class clarification, never a guess. */
export function clarificationEvents(utterance: string, nextReferenceQuestion?: string): TurnEvent[] {
  const options = [
    ...(nextReferenceQuestion ? [nextReferenceQuestion] : []),
    "Why did cash decline last week?",
    "What is PR3?",
  ];
  return [
    { type: "stage", stage: "classified", status: "completed", detail: "low confidence · 0.41" },
    {
      type: "clarification",
      clarification: {
        question:
          "I don't want to guess at that. This demo build answers a scripted set of questions — which of these did you mean?",
        options: Array.from(new Set(options)),
        reason: `“${utterance}” did not match a scripted mock turn. The real interpreter classifies against pack content; below its confidence floor it asks instead of guessing.`,
      },
    },
    {
      type: "turn_complete",
      investigationId: "inv_clarify",
      status: "clarification_required",
    },
  ];
}
