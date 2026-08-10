"""Admin bounds for session settings: what this deployment will accept.

The application composes its environment (loading ``.env`` is the entry
point's job); this module only reads a passed mapping, so tests never touch
global state — the same convention ``revi_adapter_claude.config`` follows.

Three rules shape everything here:

- **Refuse, never clamp.** An out-of-bounds setting is a
  ``POLICY_DENIED`` error naming the bound it broke. Silently lowering a
  requested budget, or silently substituting an allowed model for a
  disallowed one, produces a session whose trace says one thing and whose
  behavior is another — and the analyst is the last to know.
- **Refuse a control that would change nothing.** If the wired language
  model does not apply per-call policy (the scripted model does not),
  naming a tier would change nothing about the answer, so it is refused
  and ``/v1/capabilities`` says so before a client offers the control.
- **No bound may weaken a check.** There is no setting here that skips
  validation, suppression, or grading, and there is no env variable that
  could add one. Cost and speed are traded through model tier, evidence
  scope and ceilings only.

Environment:

- ``REVI_MODEL_TIERS`` — comma-separated allowlist of model ids a session
  may pick. Defaults to ``claude-opus-5,claude-sonnet-5``. The deployment
  pin (``REVI_MODEL_PIN``) is always allowed: a deployment necessarily
  permits the model it is already running.
- ``REVI_LLM_MAX_BUDGET_USD`` — the largest per-turn ceiling a session may
  set, and (unchanged) the per-call cap when a session sets none. Defaults
  to 0.50, matching the adapter.
- ``REVI_DEBUG_TRACE`` — ``0`` refuses decision traces outright for
  deployments that do not want them served. Defaults to on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from revi_investigation.application.planning import DEEP_TOP_N_MULTIPLIER
from revi_investigation.domain.settings import DEFAULT_SESSION_SETTINGS, SessionSettings
from revi_investigation_contracts.settings import (
    EvidenceDepth,
    NarrativeDepth,
    SessionSettingsModel,
    SettingsBoundsPayload,
)
from revi_kernel.errors import PolicyDeniedError

MODEL_TIERS_ENV = "REVI_MODEL_TIERS"
MODEL_PIN_ENV = "REVI_MODEL_PIN"
MAX_BUDGET_ENV = "REVI_LLM_MAX_BUDGET_USD"
DEBUG_TRACE_ENV = "REVI_DEBUG_TRACE"

#: Mirrors ``revi_adapter_claude.adapter.DEFAULT_MAX_BUDGET_USD``. Spelled
#: again rather than imported: the API app wires the Claude adapter through
#: exactly one sanctioned edge (``revi_api.wiring``), and a second import
#: here would widen that seam for a constant.
DEFAULT_MAX_BUDGET_USD = Decimal("0.50")

DEFAULT_MODEL_TIERS: tuple[str, ...] = ("claude-opus-5", "claude-sonnet-5")


def _positive_decimal(raw: str, name: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"{name} must be a decimal amount in USD (got {raw!r})") from None
    if value <= 0:
        raise ValueError(f"{name} must be positive (got {raw!r})")
    return value


@dataclass(frozen=True, slots=True)
class SettingsPolicy:
    """The deployment's bounds, plus the one resolver that enforces them."""

    model_tiers: tuple[str, ...]
    default_model_tier: str
    #: Whether the wired language model actually applies a per-call model
    #: override (see :attr:`ApiComponents.llm_applies_call_policy`).
    model_tier_effective: bool
    max_turn_cost_usd: Decimal
    debug_available: bool

    # -- construction ------------------------------------------------------

    @classmethod
    def from_env(
        cls, env: Mapping[str, str], *, model_tier_effective: bool = False
    ) -> SettingsPolicy:
        pin = env.get(MODEL_PIN_ENV, "").strip()
        raw_tiers = env.get(MODEL_TIERS_ENV, "").strip()
        listed = (
            tuple(part.strip() for part in raw_tiers.split(",") if part.strip())
            if raw_tiers
            else DEFAULT_MODEL_TIERS
        )
        # A deployment always allows the model it is already running.
        tiers = tuple(dict.fromkeys((*listed, pin) if pin else listed))
        raw_budget = env.get(MAX_BUDGET_ENV, "").strip()
        max_budget = (
            _positive_decimal(raw_budget, MAX_BUDGET_ENV) if raw_budget else DEFAULT_MAX_BUDGET_USD
        )
        return cls(
            model_tiers=tiers,
            default_model_tier=pin,
            model_tier_effective=model_tier_effective,
            max_turn_cost_usd=max_budget,
            debug_available=env.get(DEBUG_TRACE_ENV, "1").strip() != "0",
        )

    # -- enforcement -------------------------------------------------------

    def resolve(self, requested: SessionSettingsModel | None) -> SessionSettings:
        """Bounds-check a request and return the settings to run under.

        Raises :class:`PolicyDeniedError` — the §12 code for "the request
        was understood and is not allowed here" — naming the bound that
        was broken and what would satisfy it.
        """
        if requested is None:
            return DEFAULT_SESSION_SETTINGS
        return SessionSettings(
            model_tier=self._resolve_model_tier(requested.model_tier),
            max_turn_cost_usd=self._resolve_budget(requested.max_turn_cost_usd),
            narrative_depth=NarrativeDepth(requested.narrative_depth),
            evidence_depth=EvidenceDepth(requested.evidence_depth),
            debug=self._resolve_debug(requested.debug),
        )

    def _resolve_model_tier(self, tier: str | None) -> str | None:
        if tier is None or not tier.strip():
            return None
        tier = tier.strip()
        if not self.model_tier_effective:
            raise PolicyDeniedError(
                f"model_tier {tier!r} cannot be honored: this deployment's language model "
                "does not apply a per-call model override (it is the scripted demo model), "
                "so choosing a tier would change nothing about the answer",
                details={"setting": "model_tier", "requested": tier},
            )
        if tier not in self.model_tiers:
            allowed = ", ".join(self.model_tiers) or "(none configured)"
            raise PolicyDeniedError(
                f"model_tier {tier!r} is not in this deployment's allowlist ({allowed})",
                details={
                    "setting": "model_tier",
                    "requested": tier,
                    "allowed": list(self.model_tiers),
                },
            )
        return tier

    def _resolve_budget(self, raw: str | None) -> Decimal | None:
        if raw is None or not raw.strip():
            return None
        try:
            value = _positive_decimal(raw.strip(), "max_turn_cost_usd")
        except ValueError as exc:
            raise PolicyDeniedError(
                str(exc), details={"setting": "max_turn_cost_usd", "requested": raw}
            ) from None
        if value > self.max_turn_cost_usd:
            raise PolicyDeniedError(
                f"max_turn_cost_usd {value} exceeds this deployment's ceiling of "
                f"{self.max_turn_cost_usd} ({MAX_BUDGET_ENV})",
                details={
                    "setting": "max_turn_cost_usd",
                    "requested": str(value),
                    "ceiling": str(self.max_turn_cost_usd),
                },
            )
        return value

    def _resolve_debug(self, debug: bool) -> bool:
        if debug and not self.debug_available:
            raise PolicyDeniedError(
                f"debug traces are disabled on this deployment ({DEBUG_TRACE_ENV}=0)",
                details={"setting": "debug"},
            )
        return debug

    # -- publication -------------------------------------------------------

    def bounds_payload(self) -> SettingsBoundsPayload:
        """What ``/v1/capabilities`` publishes, so a client renders only the
        controls that exist here and would change something."""
        return SettingsBoundsPayload(
            model_tiers=list(self.model_tiers) if self.model_tier_effective else [],
            default_model_tier=self.default_model_tier,
            model_tier_effective=self.model_tier_effective,
            max_turn_cost_usd=str(self.max_turn_cost_usd),
            narrative_depths=[depth.value for depth in NarrativeDepth],
            evidence_depths=[depth.value for depth in EvidenceDepth],
            evidence_depth_deep_multiplier=DEEP_TOP_N_MULTIPLIER,
            debug_available=self.debug_available,
        )
