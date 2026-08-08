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

Utterance:

{question}
