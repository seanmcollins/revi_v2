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

Dimensions you may reference (id, and for a closed one the values it holds;
a dimension listed without values is an open one whose values live in the
data and are checked there):

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
- A SUPERLATIVE RESOLVES ON THE AXIS THE CONVERSATION IS ALREADY CUTTING.
  "Which one is worst", "which got worse", "the biggest one" name a ROW,
  not a metric: cut by the dimension the current context is broken out by,
  or — when it is broken out by nothing — by the dimension its filters
  name, and rank on the measure already in hand. Only when there is no
  dimension anywhere in the context is a superlative about the measure.
- "THE OTHER", "THE SECOND ONE", "NOT THAT ONE" point at a shown row.
  With exactly two rows on screen, "the other" is the one that is not the
  one just discussed — resolve it against the referents above and use
  drill_into or add_filter on it. Do not ask which metric, segment or
  window was meant: none of those is what was said.
- A PROPER NOUN YOU DO NOT RECOGNISE is still a filter. Put it on the
  dimension the context is already scoped by, or on the one whose closed
  values are the same kind of thing; a value this data does not hold is
  caught downstream and answered with the values it does hold, which is a
  better outcome than emitting nothing.
- If the request cannot be expressed in the closed set, emit no operators
  and say why in rationale. You may then give up to four
  clarification_options: short follow-ups the analyst could send back
  as-is, each expressible in the closed set against the context above.
  Leave the list empty rather than offer a change you could not compile.

Utterance:

{question}
