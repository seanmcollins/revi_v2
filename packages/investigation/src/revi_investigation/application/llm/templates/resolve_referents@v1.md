# Resolve referents

The analyst's follow-up may point at things already shown, by handle (F1,
D3) or by description ("the top payer", "that spike"). Map each such
mention to exactly one referent id from the live registry below. Use ONLY
listed ids — every id you return is checked against the registry and an
unknown id fails the turn.

Registry (id: label):

{referents}

Rules:

- Only resolve mentions that clearly point at a shown item; leave everything
  else out of resolutions.
- Report confidence honestly in [0, 1]; a vague pointer gets low confidence,
  never a guess.
- If nothing in the utterance refers to a shown item, return an empty
  resolutions list.

Utterance:

{question}
