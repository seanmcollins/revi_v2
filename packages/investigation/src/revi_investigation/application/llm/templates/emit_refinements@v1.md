# Emit refinement operators

Compile the analyst's follow-up into operators from the closed twelve-op
set — nothing else. The operators are applied to the current context by
deterministic code; you never compute results.

Current context:

{context}

Shown referents (id: label):

{referents}

Resolved mentions for this utterance:

{resolutions}

Dimensions you may reference (id: label):

{dimensions}

Metrics you may reference (id):

{metrics}

Rules:

- Prefer drill_into with a referent id over add_filter when the analyst
  points at shown rows or findings.
- set_window quantities are decimal strings; set_comparison with a custom
  range means a custom comparison window.
- Emit the minimal operator set that captures the request; explain your
  choice in rationale.
- If the request cannot be expressed in the closed set, emit no operators
  and say why in rationale. You may then give up to four
  clarification_options: short follow-ups the analyst could send back
  as-is, each expressible in the closed set against the context above.
  Leave the list empty rather than offer a change you could not compile.

Utterance:

{question}
