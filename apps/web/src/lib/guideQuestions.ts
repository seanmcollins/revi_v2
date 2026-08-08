/**
 * The guide questions — the product's canonical entry points, shown as
 * chips on the hero empty state and searchable from the ⌘K palette.
 *
 * Each one maps to a governed playbook in the base-rcm pack
 * (cash_decline, cob_investigation, denial_spike, cash_outlook,
 * dimension_scorecard, daily_portfolio…), so the live API answers them for
 * real. The mock driver scripts only the reference drill-down, so the
 * others land on a first-class clarification instead of a guess — which is
 * the honest demo behaviour, not a failure.
 */
export const GUIDE_QUESTIONS: readonly string[] = [
  "What is PR3?",
  "Give me my denial rate by month for the last 6 months",
  "Do I have a COB problem?",
  "What are my top 5 issues?",
  "Will my cash increase next month?",
  "Assess the performance of each facility financially",
  "Give me payer payments by payer category weekly over the last 3.25 months",
  "Drill into Medicaid",
] as const;
