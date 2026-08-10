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

/**
 * The hero's four — one per verb of a working day: detect, diagnose,
 * act, prevent. A reader who clicks all four has walked the whole
 * product loop. The full guide set above stays reachable from ⌘K; it
 * is the regression anchor list, not the front door.
 */
export const HERO_QUESTIONS: readonly string[] = [
  "Where are denials rising, and which payers are driving it?",
  "Why did cash come in low last week?",
  "What should my team work on first today?",
  "Is anything about to miss a filing deadline?",
] as const;
