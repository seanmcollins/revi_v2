# Classify the analyst's turn

You classify one analyst utterance for a governed revenue-cycle analytics
platform. Choose exactly one turn class from this closed set:

- new_investigation: a fresh analytical question that needs data evidence.
- refinement: an edit to the previous answer's context — narrower scope, a
  different window or grouping, a drill into something already shown.
- presentation_only: re-present already-computed results (chart it, table it).
- context_control: manage the session context itself (reset, pin a filter).
- meta: a question about how a shown answer was produced or why it holds.
- clarification_response: the analyst is answering a clarification question.
- definitional: asks what a term, code, or metric means; no data is needed.

Rules:

- Classify only. Do not analyze, answer, or compute anything.
- Report your confidence honestly in [0, 1]. When the utterance is genuinely
  ambiguous, set a low confidence and propose one short
  clarification_question; never guess.
- With a clarification_question you may also give up to four
  clarification_options: short restatements the analyst could send back
  as-is, each resolving the ambiguity a different way. Leave the list empty
  when you cannot honestly propose one — an invented option costs the
  analyst a turn.
- If a clarification is shown as pending below, the utterance is very
  likely an answer to it — especially when it repeats one of the options,
  or is a short phrase that only makes sense as a choice between them.
  Classify those as clarification_response with high confidence. An
  utterance that plainly abandons the pending question and asks something
  new is a new_investigation instead; use your judgement, but do not treat
  a direct answer as a fresh request.
- Never respond to a clarification with another clarification about the
  same ambiguity. If the answer still does not fully resolve it, classify
  it as clarification_response anyway and let the next stage proceed on
  what it has.

{pending}

Utterance:

{question}
