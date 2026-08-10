/**
 * Warehouse identifiers → the words an analyst uses.
 *
 * Lifted out of `lib/contract.ts` so `lib/warnings.ts` can reach it: the
 * contract module already imports the warning deduper, and a warning
 * sentence that leaks `denial_rate (portfolio_denial_trend, 1 row(s))`
 * onto the answer needs the same spelling rules the chart axes use. One
 * table, one function, no cycle.
 *
 * `contract.ts` re-exports `humanizeColumn` so every existing importer is
 * untouched.
 */

const COLUMN_ACRONYMS: Record<string, string> = {
  carc: "CARC",
  rarc: "RARC",
  ar: "AR",
  dnfb: "DNFB",
  cpt: "CPT",
  drg: "DRG",
  msdrg: "MS-DRG",
  npi: "NPI",
  pct: "%",
};

export function humanizeColumn(column: string): string {
  const words = column.split(/[_\s]+/).filter(Boolean);
  if (words.length === 0) return column;
  const spelled = words.map((word) => COLUMN_ACRONYMS[word.toLowerCase()] ?? word);
  const [head, ...tail] = spelled;
  const lead = COLUMN_ACRONYMS[words[0].toLowerCase()] ? head : head[0].toUpperCase() + head.slice(1);
  return [lead, ...tail].join(" ");
}

/**
 * The same spelling, mid-sentence.
 *
 * `humanizeColumn` capitalizes because it writes titles and axis labels;
 * a measure named inside a sentence ("nothing above speaks for them:
 * denial rate; cash posted") must not start a capital in the middle of a
 * clause. Acronyms keep their case either way — "AR over 90 %" is not
 * improved by "ar over 90 %".
 */
export function humanizeInline(column: string): string {
  const spelled = humanizeColumn(column);
  const first = column.split(/[_\s]+/).filter(Boolean)[0]?.toLowerCase() ?? "";
  if (COLUMN_ACRONYMS[first]) return spelled;
  return spelled[0]?.toLowerCase() + spelled.slice(1);
}
