# Choose the research angles for a recoverability review

An analyst wants to know what is most likely to be recovered out of the
denials that are still open. Your only job is to choose which angles to look
at, and how to cut each one. You never compute anything: every number in the
report comes from deterministic estimators that run after you.

The population under review: {population}

The angle catalogue — you may use ONLY these families:

- `expected_recovery` — price the open denials against each population's own
  measured recovery rate. This is the headline and is always run: include it
  EXACTLY ONCE and choose how to cut it. Two pricings of the same denials
  produce two different totals, so only the finest cut you ask for is run.
- `outcome_by_stratum` — recovery rate by population, with the size of each.
- `payer_contrast` — the strongest payer against the weakest, with the test
  that separates them.
- `class_contrast` — the strongest denial type against the weakest.
- `timeliness_curve` — recovery rate by how many days passed before the
  denial went back out.
- `deadline_interaction` — what crossing the filing deadline costs, split by
  whether the plan's limit is confirmed or still a planning default. Takes no
  parameters.

The populations an angle may be cut by — ONLY these:

`payer`, `plan`, `recovery_class` (the kind of denial: coding, registration,
routing, clinical, final), `age_band` (how long the denial has been sitting),
`dollar_band` (the size of the denied amount), `delay_band` (how long before
it was resubmitted), `filing_position` (inside or past the filing deadline),
`filing_rule` (whether that limit is confirmed).

How to fill each angle:

- `stratify_by` — for `expected_recovery` and `outcome_by_stratum`, one or
  two populations. Two is a finer cut and refuses more cells for being too
  small; prefer one unless the question asks for the interaction.
- `within` — for `payer_contrast` and `class_contrast`, at most one
  population to hold fixed (`recovery_class` for a payer contrast, `payer`
  for a class contrast). For `timeliness_curve`, at most one of
  `recovery_class` or `payer`. Leave it empty for the pooled reading.
- `basis` — `decided` answers "when we resubmit and the payer answers, how
  often do we win". `pursuit` answers "is this even being worked". Use
  `pursuit` only on `outcome_by_stratum`, and only when the question is
  about effort rather than outcome. `expected_recovery` is always `decided`.

Rules:

- Between four and eight angles. Fewer is a thin report; more takes longer
  than it is worth and repeats itself.
- No two angles identical in family, cuts and basis.
- Cover the question that was asked FIRST, then the standing ones. A question
  about one payer still wants the timeliness curve and the deadline, because
  those are what the reader can act on.
- `research_question` — restate what this run answers, in one sentence, in
  the analyst's own words. If they asked nothing specific, write the standing
  question: what is most likely to be recovered out of what is still open.
- `rationale` — one or two sentences on why these angles and not others.

The analyst's question:

{question}
