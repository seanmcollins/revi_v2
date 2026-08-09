# Interpret the analyst's question

Map the analyst's question onto the governed vocabulary below. Use ONLY the
listed ids — never invent metrics, dimensions, playbooks, or concepts. Every
id you return is validated against governed content; unknown ids fail the
turn.

Metrics (id: description):

{metrics}

Dimensions (id: label):

{dimensions}

Playbooks (id: description):

{playbooks}

Concepts (id: name):

{concepts}

Date bases: {date_bases}

Guidance:

- Prefer a playbook when the question matches one's purpose; leave
  metric_ids empty in that case unless specific metrics are also named.
- window: THREE shapes, and the analyst's own words pick which one.
  - relative — `{"quantity": "6", "unit": "month", "mode": "full_periods"}`.
    quantity is a decimal string ("1", "6", "3.25"); mode is trailing
    (window ends at the newest data), full_periods (last completed calendar
    periods), or to_date (current period so far). Use it for "the last 6
    months", "last quarter", "this month so far", "year to date"
    (`{"quantity": "1", "unit": "year", "mode": "to_date"}`).
  - named calendar period — `{"unit": "month", "year": 2026, "index": 6}`
    for "June 2026", `{"unit": "quarter", "year": 2026, "index": 2}` for
    "Q2 2026", `{"unit": "year", "year": 2025}` for "2025" or "fiscal 2025"
    stated as a calendar year. index is the month (1-12) or quarter (1-4);
    omit it for a year. Use this whenever the analyst NAMES a period —
    never convert it to dates yourself.
  - explicit dates — `{"start": "2026-01-01", "end": "2026-06-30"}`, only
    when the analyst states the dates themselves.
  Omit the window entirely when the analyst gives none.
- time_grain: day, week or month — set it when the analyst asks for a
  breakdown OVER TIME ("by month", "monthly", "week over week", "trend
  over the last 6 months"). It is the time axis, not a dimension: "denial
  rate by month by payer" sets time_grain=month AND dimension_ids=[payer].
  Leave it null when the question wants one number for the whole period.
- comparison: prior_period or prior_year, only when the question asks for
  one (explicitly or via words like "decline", "change", "vs").
- scope: only predicates the analyst states, on listed dimension ids. Use
  the value EXACTLY as the analyst wrote it — never normalize, expand or
  guess a code; unmatched values are checked against the data and reported
  back to the analyst, and an invented value hides that check.
- direction: set it only when the question asks about a MOVEMENT and says
  which way — "biggest increase" (increase), "which payers dropped"
  (decrease), "who got worse" (worsened), "where did we improve"
  (improved). A question that asks "what changed" or "which is biggest"
  asserts no direction: leave it null.
- magnitude: largest or smallest, only when the question phrases one over
  that direction ("biggest increase" → largest, "smallest decline" →
  smallest). Leave null otherwise.
- direction_asserted: true when the question STATES the movement as a fact
  and asks about its cause — "why did denials double", "what drove the
  spike in write-offs", "explain the 20% drop". False (the default) when
  the question asks WHICH cells moved that way — "which payers had the
  biggest increase", "where did denials rise". The distinction is between a
  premise to be checked and a query to be run; it is never about how
  confident the wording sounds.
- order: best_first or worst_first, only when the question asks for a
  ranking IN AN ORDER — "rank payers best to worst" (best_first), "worst
  performers first" (worst_first). Do not translate it into a direction of
  sort; "best" and "worst" are resolved against the metric's own polarity
  downstream. Leave null when no order is asked for.
- If the question only asks what something means, put the terms in
  definitional_terms and leave the analytical fields empty.
- If the question cannot be mapped to this vocabulary, set clarification to
  one short question instead of guessing.
- With a clarification you may also give up to four clarification_options.
  Each is an object: a `label` the analyst could send back as-is, plus the
  governed ids that option would actually use (`metric_ids`,
  `dimension_ids`, `playbook_id`, `scope`). Every id is re-checked against
  the pack and catalog and the option is DROPPED if any of them does not
  resolve — so an option whose ids you cannot fill in from the vocabulary
  above is one the analyst will never see. Leave the list empty rather than
  offer something this vocabulary cannot answer.

Question:

{question}
