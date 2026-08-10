"use client";

import { Fragment } from "react";

import { ReferentChip } from "@/components/answer/ReferentChip";
import { cn } from "@/lib/utils";

/**
 * Referent citations, in both spellings that reach this component.
 *
 * The server's narrative validator is `\b[FD]\d+\b` (see
 * `revi_presentation.narrative`) — it redacts any sentence that states
 * figures without a BARE `F2`, and every live narrative therefore cites
 * that way. This regex used to match only the bracketed `[F2]` form,
 * which is the fixture's spelling, so on a live answer not one citation
 * ever became a chip. Both forms are accepted; the prefix is restricted
 * to F and D (findings and dimension values) exactly as the server's is,
 * so ordinary prose is not shredded into chips.
 */
const REFERENT_TOKEN = /\[([FD]\d+)\]|\b([FD]\d+)\b/g;

/** Between two citations of one cluster: ", ", " and ", " & ". */
const CLUSTER_JOIN = /^[\s,]*(?:and|&)?[\s,]*$/i;

interface Token {
  value: string;
  start: number;
  end: number;
}

interface Cluster {
  values: string[];
  /** Span consumed from the source text, parentheses included. */
  start: number;
  end: number;
  /** The run was written as a parenthetical: "(F1, F2, F3)". */
  parenthetical: boolean;
}

/**
 * Citations, grouped the way a reader reads them.
 *
 * Measured on the live worklist turn: ~160 words of folded prose carrying
 * TWELVE monospace markers, three of them the same "(F1, F2, F3)". In the
 * calm layout the prose IS the answer, so that is the reading surface,
 * and twelve green boxed chips in one paragraph is the detail spam this
 * whole change exists to remove — moved into the sentences.
 *
 * Two rules, both conservative:
 *
 *   a RUN of consecutive citations is one cluster. "(F1, F2, F3)" is one
 *     citation of three facts, not three citations, and it renders as one
 *     quiet superscript group — three tap targets, one visual mark. What
 *     is consumed with the run is only what separates its members (a
 *     comma, an "and", the parentheses around the whole); a run is never
 *     extended across a word that carries meaning, so no sentence loses
 *     anything but its own punctuation.
 *   a parenthetical whose facts have ALL been cited already is dropped,
 *     parentheses and all. Nothing is dropped the first time it is said,
 *     a cluster carrying anything new is kept whole, and a bare in-grammar
 *     citation ("F1 rose while F2 fell") is never touched — removing one
 *     of those would edit the server's sentence rather than its apparatus.
 *
 * The prose itself is unchanged: `model.prose.text` is what the copied
 * answer and every export carry, citations included.
 */
export function narrativeClusters(text: string): Cluster[] {
  const tokens: Token[] = [];
  for (const match of text.matchAll(REFERENT_TOKEN)) {
    tokens.push({
      value: match[1] ?? match[2],
      start: match.index,
      end: match.index + match[0].length,
    });
  }

  const clusters: Cluster[] = [];
  let i = 0;
  while (i < tokens.length) {
    let j = i;
    while (
      j + 1 < tokens.length &&
      CLUSTER_JOIN.test(text.slice(tokens[j].end, tokens[j + 1].start))
    ) {
      j += 1;
    }
    const run = tokens.slice(i, j + 1);
    let start = run[0].start;
    let end = run[run.length - 1].end;
    // The parentheses belong to the citation, not to the sentence: a
    // superscript group inside brackets is a footnote wearing a box.
    const opensAt = start - 1;
    const parenthetical =
      opensAt >= 0 && text[opensAt] === "(" && text[end] === ")";
    if (parenthetical) {
      start = opensAt;
      end += 1;
    }
    const values: string[] = [];
    for (const token of run) if (!values.includes(token.value)) values.push(token.value);
    clusters.push({ values, start, end, parenthetical });
    i = j + 1;
  }
  return clusters;
}

/**
 * Narrative with inline referent citations: "F1" / "[F1]" tokens become
 * live referent chips (hover card, click-to-open-the-fact).
 */
export function NarrativeText({
  text,
  streaming,
  size = "body",
}: {
  text: string;
  streaming?: boolean;
  /**
   * `lead` is the calm layout's setting: the write-up is the answer
   * there, so it is set at reading size with a measure to match. `body`
   * is what the other two use, where the prose sits among cards.
   */
  size?: "body" | "lead";
}) {
  const clusters = narrativeClusters(text);
  const parts: Array<
    { kind: "text"; value: string } | { kind: "cite"; values: string[] }
  > = [];
  const cited = new Set<string>();
  let last = 0;

  for (const cluster of clusters) {
    const repeat =
      cluster.parenthetical && cluster.values.every((value) => cited.has(value));
    // A dropped parenthetical takes the space in front of it with it, so
    // the sentence closes up rather than carrying a double space.
    const upto = repeat && text[cluster.start - 1] === " " ? cluster.start - 1 : cluster.start;
    if (upto > last) parts.push({ kind: "text", value: text.slice(last, upto) });
    if (!repeat) parts.push({ kind: "cite", values: cluster.values });
    for (const value of cluster.values) cited.add(value);
    last = cluster.end;
  }
  if (last < text.length) parts.push({ kind: "text", value: text.slice(last) });

  return (
    <p
      className={cn(
        "text-pretty",
        size === "lead"
          ? "text-lead leading-[1.7] text-foreground"
          : "text-body leading-[1.65] text-foreground/90",
        streaming && "stream-caret",
      )}
    >
      {parts.map((part, i) =>
        part.kind === "cite" ? (
          <span key={`cite-${i}`} className="whitespace-nowrap">
            {part.values.map((value, k) => (
              <Fragment key={value}>
                {k > 0 && (
                  <span aria-hidden className="align-super text-[0.62em] text-muted-foreground">
                    ,
                  </span>
                )}
                <ReferentChip value={value} tone="citation" />
              </Fragment>
            ))}
          </span>
        ) : (
          <Fragment key={i}>{part.value}</Fragment>
        ),
      )}
    </p>
  );
}
