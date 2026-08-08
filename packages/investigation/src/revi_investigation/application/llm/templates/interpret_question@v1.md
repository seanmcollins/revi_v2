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
- scope: only predicates the analyst states, on listed dimension ids.
- If the question only asks what something means, put the terms in
  definitional_terms and leave the analytical fields empty.
- If the question cannot be mapped to this vocabulary, set clarification to
  one short question instead of guessing.

Question:

{question}
