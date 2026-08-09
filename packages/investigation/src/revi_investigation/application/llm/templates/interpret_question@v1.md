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
- window: quantity is a decimal string ("1", "6", "3.25"); mode is
  trailing (window ends today), full_periods (last completed calendar
  periods), or to_date (current period so far). Omit the window when the
  analyst gives none.
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
