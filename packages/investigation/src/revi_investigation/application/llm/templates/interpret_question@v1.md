# Interpret the analyst's question

Map the analyst's question onto the governed vocabulary below. Use ONLY the
listed ids — never invent metrics, dimensions, playbooks, or concepts. Every
id you return is validated against governed content; unknown ids fail the
turn.

Metrics (id: description, then `can be broken out by:` — the ONLY dimensions
this metric may be cut by; proposing any other one fails the question):

{metrics}

Dimensions (id: label):

{dimensions}

Playbooks (id: description, then `asked as:` — phrasings the pack author
recorded for how analysts ask for this one; they are examples, not patterns,
and a question that means the same thing in other words still matches):

{playbooks}

Concepts (id: name):

{concepts}

Date bases: {date_bases}

Guidance:

- THE CONVERSATION COMES FIRST. The answer on screen (below) is what this
  question was asked against. When the question names no period, no
  measure, or no scope, it means the one the conversation has been reading
  — say the SAME metric ids the answer on screen measured when the question
  is plainly still about them, and OMIT the window rather than inventing
  one, so it can be carried. Only name a window the analyst's own words
  name.
- Prefer a playbook when the question matches one's purpose; leave
  metric_ids empty in that case unless specific metrics are also named.
- answer_shape: what the answer's FIRST SENTENCE owes this question. One of
  verdict, entity, scalar, cause, trend, comparison, definition, worklist.
  - verdict — a yes/no question: "do we owe refunds?", "are we at risk of
    losing revenue to auth denials?", "do I have a COB problem?", "is
    anything about to miss a filing deadline?", "are any payers paying
    below contract?"
  - entity — asks WHICH one: "which payer denies us most", "where are
    denials rising"
  - scalar — asks HOW MUCH or HOW MANY, wanting one number: "how much did
    we write off last month", "what was our denial rate", "how many claims
    are unbilled"
  - cause — asks WHY: "why did cash come in low", "what's driving the
    increase"
  - trend — asks for a series over time: "denial rate by month", "A/R over
    90 by month this year"
  - comparison — asks one period or population against another: "this
    quarter vs last on collections"
  - definition — asks what a term means
  - worklist — asks what to work on: "what should my team do first today"
  Set it on every analytical question. A question that is both ("are
  denials rising, and which payers are driving it") takes the shape of its
  FIRST clause — here, verdict.
- subject_metric: the single metric id from the list above that this
  question is ABOUT — the one whose number the reader came for. Pick it
  even when you route to a playbook: "where are denials rising" is about
  `denial_rate`, not about denied dollars; "how often do we win appeals" is
  about `appeal_overturn_rate`, not overturned dollars; "our A/R keeps
  creeping up" is about `ar_balance`, not charges. It must be a metric the
  question's own reading would read — one of metric_ids, or a metric the
  chosen playbook measures. Leave it null if the question names no single
  subject.
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

Deep research (`deep_research`): set this ONLY when the analyst asks for the
recoverability review by name — "run deep research on what we can recover",
"do a deep dive on Northbridge's denials", "what's actually recoverable out
of the open denials". It names WHICH denials and nothing else: `population`
is one of `all_open`, `payer`, `recovery_class` or `facility`, and `values`
holds the names the analyst used (empty for every open denial). Setting it
does not replace the answer — the question still gets answered — it offers
the run beside it. Leave it null for an ordinary question about denials or
recoveries, however detailed.

{conversation}

Question:

{question}
