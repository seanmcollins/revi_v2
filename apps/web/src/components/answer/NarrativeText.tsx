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

/**
 * Narrative with inline referent citations: "F1" / "[F1]" tokens become
 * live referent chips (hover card, click-to-focus).
 */
export function NarrativeText({
  text,
  streaming,
}: {
  text: string;
  streaming?: boolean;
}) {
  const parts: Array<{ kind: "text"; value: string } | { kind: "ref"; value: string }> = [];
  let last = 0;
  for (const match of text.matchAll(REFERENT_TOKEN)) {
    if (match.index > last) parts.push({ kind: "text", value: text.slice(last, match.index) });
    parts.push({ kind: "ref", value: match[1] ?? match[2] });
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", value: text.slice(last) });

  return (
    <p
      className={cn(
        "text-[0.83rem] leading-[1.65] text-foreground/90",
        streaming && "stream-caret",
      )}
    >
      {parts.map((part, i) =>
        part.kind === "ref" ? (
          <ReferentChip key={`${part.value}-${i}`} value={part.value} className="mx-0.5 align-[0.08em]" />
        ) : (
          <Fragment key={i}>{part.value}</Fragment>
        ),
      )}
    </p>
  );
}
