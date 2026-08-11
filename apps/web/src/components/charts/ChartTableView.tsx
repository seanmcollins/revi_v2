"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { ChartRow, ChartSpec } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * THE ROWS, AS ROWS — the form that can carry every mark this product has.
 *
 * It is offered on every figure, and not as a consolation. The palette
 * this product draws entities in leans on relief (a hue is a tracking aid,
 * never an identity), and the dataviz rule for a palette that leans on
 * relief is that a table view exists. So does the rule for a figure a
 * reader cannot see: a bar chart read by somebody using a screen reader is
 * an image with a caption, and this is the same certified rows in a
 * structure that can be read out.
 *
 * EVERY MARK TRAVELS, IN THE CELL. That is the whole reason this is not a
 * generic data grid: the "≤" opens the numeral exactly as it does on the
 * axis, a withheld cell says so in words instead of printing a blank a
 * reader takes for a zero, an absence says which window it is absent from,
 * and a provisional row keeps its star. A table that dropped them would be
 * the most dangerous form on the switcher, because a spreadsheet-shaped
 * thing is the one a reader trusts without checking.
 *
 * THE NUMERALS ARE RIGHT-ALIGNED AND TABULAR (`num`), which is not
 * decoration: a column of figures a reader compares by eye has to line up
 * on its decimal point, and the product's own numeral class is what does
 * that everywhere else.
 */
export function ChartTableView({
  spec,
  seriesLabel,
  formatValue,
  onDrill,
  absentIn,
  subject,
}: {
  spec: ChartSpec;
  seriesLabel: (key: string) => string;
  formatValue: (value: number) => string;
  onDrill: (row: ChartRow) => void;
  /** Per (row, series): the compare join's zero, not a reading. */
  absentIn: (row: ChartRow, key: string) => boolean;
  /** The row the answer is about — the same emphasis the marks carry. */
  subject?: string;
}) {
  const bounded = (row: ChartRow, key: string): boolean =>
    row.cells !== undefined ? row.cells[key]?.bounded === true : row.bounded === true;

  return (
    <div className="max-h-[26rem] w-full overflow-auto rounded-md border">
      <Table className="text-meta">
        <TableHeader>
          <TableRow className="bg-surface-sunken/60">
            {/* The dimension's own name, or the axis caption's fallback. A
                header reading "label" would be this client's object model
                on the one surface that looks like a spreadsheet. */}
            <TableHead className="h-8 px-2 text-micro font-medium">Category</TableHead>
            {spec.series.map((s) => (
              <TableHead key={s.key} className="h-8 px-2 text-right text-micro font-medium">
                {seriesLabel(s.key)}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {spec.rows.map((row) => (
            <TableRow
              key={row.label}
              {...(row.label === subject ? { "data-subject": "true" } : {})}
              className={cn(row.label === subject && "bg-accent/40")}
            >
              <TableCell className="p-0 align-middle">
                {/* A REAL BUTTON, and the same gesture the bar carries.
                    "Switching is a pure re-render of the same certified
                    rows" is only true if the affordances survive it — a
                    table whose rows cannot be drilled would be a form
                    that quietly costs the reader the drill. */}
                <button
                  type="button"
                  onClick={() => onDrill(row)}
                  // The visible text is the row's name; the ACCESSIBLE name
                  // is the name plus what clicking it does. A control read
                  // aloud as "State Medicaid MCO, button" does not say that
                  // it starts a new question.
                  aria-label={`Drill into ${row.label}`}
                  title={`Drill into ${row.label}`}
                  className="focus-ring block w-full px-2 py-1.5 text-left hover:underline"
                >
                  {row.label}
                  {row.provisional === true && <span aria-hidden>{" *"}</span>}
                </button>
              </TableCell>
              {spec.series.map((s) => {
                const raw = row.values[s.key];
                const absent = absentIn(row, s.key);
                return (
                  <TableCell key={s.key} className="num px-2 py-1.5 text-right">
                    {row.withheld === true ? (
                      // NOT A BLANK. A blank cell in a table is read as a
                      // zero by every reader who has ever opened a
                      // spreadsheet, and this one is a refusal.
                      <span className="font-normal text-muted-foreground">Withheld †</span>
                    ) : absent ? (
                      <span className="font-normal text-muted-foreground">No figure ‡</span>
                    ) : typeof raw === "number" ? (
                      <>
                        {bounded(row, s.key) ? "≤ " : ""}
                        {formatValue(raw)}
                      </>
                    ) : (
                      <span className="font-normal text-muted-foreground">No figure</span>
                    )}
                  </TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
