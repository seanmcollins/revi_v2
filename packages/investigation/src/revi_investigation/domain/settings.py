"""Session settings as the engine sees them (design §7.1).

The wire shape lives in ``revi_investigation_contracts.settings``; this is
its typed, already-validated twin. Nothing here parses or bounds-checks:
by the time settings reach the engine the deployment's admin bounds have
already accepted or REFUSED them, so an engine that holds a
:class:`SessionSettings` is holding values a deployment agreed to run.

``max_turn_cost_usd is None`` is meaningfully different from a large
number: it means "no per-turn ledger", which is exactly the behavior that
existed before the control did — each call bounded by the deployment's
per-call ceiling and nothing counting the turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from revi_investigation_contracts.settings import EvidenceDepth, NarrativeDepth


@dataclass(frozen=True, slots=True)
class SessionSettings:
    """The controls in force for a session (or, per-turn, for one turn)."""

    #: Model id every LLM call runs on; ``None`` keeps the deployment pin.
    model_tier: str | None = None
    #: Ceiling on the TOTAL LLM spend of one turn; ``None`` runs no ledger.
    max_turn_cost_usd: Decimal | None = None
    narrative_depth: NarrativeDepth = NarrativeDepth.SUMMARY
    evidence_depth: EvidenceDepth = EvidenceDepth.STANDARD
    debug: bool = False

    def __post_init__(self) -> None:
        # Normalize the depths to real enum members. A store that hands back
        # the bare string (a ``StrEnum`` member IS a ``str``, so an older
        # JSONB row holds "deep", not the member) would otherwise leave an
        # identity check downstream — ``depth is EvidenceDepth.STANDARD`` in
        # the planner — quietly taking the wrong branch, and planning a
        # deeper sweep than the session asked for. The type says
        # ``NarrativeDepth``; this makes that true whatever built it.
        object.__setattr__(self, "narrative_depth", NarrativeDepth(self.narrative_depth))
        object.__setattr__(self, "evidence_depth", EvidenceDepth(self.evidence_depth))
        if self.max_turn_cost_usd is not None and self.max_turn_cost_usd <= 0:
            raise ValueError("max_turn_cost_usd must be positive when set")


DEFAULT_SESSION_SETTINGS = SessionSettings()
