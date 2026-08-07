# revi-pack-learning — reserved seat

This package is intentionally empty. It reserves the workspace seat for the design doc's Phase 4
**Pack Learning and Evaluation** capability (design §4.5, §9): mining investigation traces, refinement
sequences, and typed analyst corrections into atomic `PackDelta` proposals that flow through the
observe → propose → validate → replay → shadow → promote pipeline.

It exists now so that:
- import-linter contracts already name it (its independence from other capabilities is enforced from day one);
- the typed `PackDelta` / `AnalystCorrection` shapes in `revi_pack` have a declared future consumer.

Nothing may import from this package until Phase 4 lands.
