"""Session-scoped settings and the admin bounds that govern them.

Every field here names a control that **changes computation**. There is no
cosmetic knob in this module and there is no control that weakens a
correctness check: speed and cost are traded through model tier, evidence
scope and cost ceilings, never through skipping validation.

- ``model_tier`` — the model id every LLM call of the session runs on,
  threaded onto :class:`~revi_investigation.application.ports.StructuredLlmRequest`
  and ``TextLlmRequest`` and applied by the adapter. Bounded by a
  deployment allowlist.
- ``max_turn_cost_usd`` — a ceiling on the LLM spend of ONE turn. The
  engine keeps a per-turn ledger and derives each call's budget from what
  is left; running out stops the turn honestly (a clarification naming the
  ceiling), never a quiet downgrade to a cheaper answer.
- ``narrative_depth`` — a real parameter of narrative composition: it
  selects which versioned template is rendered, so the model is asked for
  a different piece of writing. Grounding validation is identical in both
  depths.
- ``evidence_depth`` — a real planner parameter: ``deep`` scales the
  *platform's own* top-N cutoffs (the pack playbook's ``top_n``) so fewer
  probes truncate. An analyst's explicit limit is never rescaled.
- ``debug`` — attaches the turn's full decision trace to the response.

Money is a decimal **string** on the wire (``"0.25"``), matching
``UsageSummary.cost_usd`` and ``BenchmarkPayload`` — a budget rounded
through a float is a budget that is not the one that was asked for.

Out-of-bounds settings are REFUSED with a message naming the bound. They
are never silently clamped: a session that believes it is running on a
model it is not running on is a session whose trace lies.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from revi_investigation_contracts.refinements import ClosedModel


class NarrativeDepth(StrEnum):
    """How much the narrative composer is asked to write."""

    #: Two short paragraphs — the default answer voice.
    SUMMARY = "summary"
    #: Full analyst detail: every certified finding, its grade, the
    #: reconciliation verdict, and benchmark context where the pack has it.
    ANALYST = "analyst"


class EvidenceDepth(StrEnum):
    """How wide the platform's own top-N cutoffs are planned."""

    #: Pack-authored ``top_n`` exactly as written.
    STANDARD = "standard"
    #: Pack-authored ``top_n`` scaled by the deployment's deep multiplier.
    DEEP = "deep"


class SessionSettingsModel(ClosedModel):
    """The settings a caller applies to a session (or to one turn)."""

    #: Model id for this session's LLM calls; ``None`` keeps the
    #: deployment pin (``REVI_MODEL_PIN``).
    model_tier: str | None = None
    #: Ceiling on the TOTAL LLM spend of one turn, as a decimal string.
    #: ``None`` keeps the deployment's per-call ceiling and runs no
    #: per-turn ledger (exactly the behavior before this field existed).
    max_turn_cost_usd: str | None = None
    narrative_depth: NarrativeDepth = NarrativeDepth.SUMMARY
    evidence_depth: EvidenceDepth = EvidenceDepth.STANDARD
    #: Attach the turn's decision trace to the response.
    debug: bool = False


class SettingsBoundsPayload(ClosedModel):
    """What a deployment will accept — published so a UI can render the
    controls honestly instead of guessing and discovering refusals."""

    #: Model ids this deployment allows (``REVI_MODEL_TIERS``).
    model_tiers: list[str] = Field(default_factory=list)
    #: The pin used when a session names no tier (``REVI_MODEL_PIN``).
    default_model_tier: str = ""
    #: Whether the wired language model actually applies a per-call model
    #: override. False in scripted-demo mode: the script is not a model,
    #: so choosing a tier there would change nothing and the UI must not
    #: pretend otherwise.
    model_tier_effective: bool = False
    #: The largest per-turn ceiling a session may set, decimal string
    #: (``REVI_LLM_MAX_BUDGET_USD``).
    max_turn_cost_usd: str = "0"
    narrative_depths: list[str] = Field(
        default_factory=lambda: [d.value for d in NarrativeDepth]
    )
    evidence_depths: list[str] = Field(default_factory=lambda: [d.value for d in EvidenceDepth])
    #: What ``evidence_depth=deep`` multiplies pack top-N cutoffs by.
    evidence_depth_deep_multiplier: int = 1
    #: Whether this deployment will serve decision traces at all.
    debug_available: bool = False
