"""Admin bounds for session settings: refused loudly, never clamped.

The refusal is the feature. A deployment that quietly lowered a requested
budget, or quietly ignored a model tier it could not honor, would hand
back a session whose trace and whose behavior disagree — and the analyst
would be the last to find out.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from revi_api.settings_policy import SettingsPolicy
from revi_investigation_contracts.settings import (
    EvidenceDepth,
    NarrativeDepth,
    SessionSettingsModel,
)
from revi_kernel.errors import PolicyDeniedError

LIVE_ENV = {"REVI_MODEL_PIN": "claude-opus-5", "REVI_LLM_MAX_BUDGET_USD": "0.50"}


def _policy(**env: str) -> SettingsPolicy:
    return SettingsPolicy.from_env({**LIVE_ENV, **env}, model_tier_effective=True)


class TestDefaults:
    def test_no_request_is_the_default_posture(self) -> None:
        settings = _policy().resolve(None)

        assert settings.model_tier is None  # the deployment pin
        assert settings.max_turn_cost_usd is None  # no per-turn ledger
        assert settings.narrative_depth is NarrativeDepth.SUMMARY
        assert settings.evidence_depth is EvidenceDepth.STANDARD
        assert settings.debug is False

    def test_the_deployment_pin_is_always_allowed(self) -> None:
        """A deployment necessarily permits the model it is already
        running, whatever the allowlist happens to spell."""
        policy = SettingsPolicy.from_env(
            {"REVI_MODEL_PIN": "claude-opus-9", "REVI_MODEL_TIERS": "claude-sonnet-5"},
            model_tier_effective=True,
        )

        resolved = policy.resolve(SessionSettingsModel(model_tier="claude-opus-9"))

        assert resolved.model_tier == "claude-opus-9"


class TestModelTier:
    def test_a_tier_outside_the_allowlist_is_refused(self) -> None:
        with pytest.raises(PolicyDeniedError) as caught:
            _policy(REVI_MODEL_TIERS="claude-opus-5,claude-sonnet-5").resolve(
                SessionSettingsModel(model_tier="some-other-model")
            )

        assert "allowlist" in caught.value.message
        assert caught.value.details["allowed"] == [
            "claude-opus-5",
            "claude-sonnet-5",
        ]

    def test_a_tier_is_refused_when_the_model_cannot_honor_it(self) -> None:
        """Scripted-demo mode. Accepting the tier and ignoring it would be
        a control that changes nothing — the exact thing this codebase is
        not allowed to ship."""
        policy = SettingsPolicy.from_env(LIVE_ENV, model_tier_effective=False)

        with pytest.raises(PolicyDeniedError) as caught:
            policy.resolve(SessionSettingsModel(model_tier="claude-sonnet-5"))

        assert "would change nothing" in caught.value.message

    def test_an_empty_tier_is_absent_not_invalid(self) -> None:
        assert _policy().resolve(SessionSettingsModel(model_tier="  ")).model_tier is None


class TestBudget:
    def test_within_the_ceiling_is_honored_exactly(self) -> None:
        resolved = _policy().resolve(SessionSettingsModel(max_turn_cost_usd="0.25"))

        assert resolved.max_turn_cost_usd == Decimal("0.25")

    def test_over_the_ceiling_is_refused_not_clamped(self) -> None:
        with pytest.raises(PolicyDeniedError) as caught:
            _policy().resolve(SessionSettingsModel(max_turn_cost_usd="5.00"))

        assert caught.value.details["ceiling"] == "0.50"
        assert caught.value.details["requested"] == "5.00"

    @pytest.mark.parametrize("value", ["0", "-1", "abc", "0.0"])
    def test_a_nonsense_budget_is_refused(self, value: str) -> None:
        with pytest.raises(PolicyDeniedError):
            _policy().resolve(SessionSettingsModel(max_turn_cost_usd=value))

    def test_an_unset_budget_runs_no_ledger(self) -> None:
        assert _policy().resolve(SessionSettingsModel()).max_turn_cost_usd is None


class TestDebug:
    def test_debug_is_refused_when_the_deployment_disables_it(self) -> None:
        policy = SettingsPolicy.from_env(
            {**LIVE_ENV, "REVI_DEBUG_TRACE": "0"}, model_tier_effective=True
        )

        with pytest.raises(PolicyDeniedError):
            policy.resolve(SessionSettingsModel(debug=True))
        # and asking for no debug is still fine
        assert policy.resolve(SessionSettingsModel()).debug is False


class TestPublishedBounds:
    def test_bounds_describe_what_the_deployment_will_accept(self) -> None:
        bounds = _policy().bounds_payload()

        assert bounds.model_tiers == ["claude-opus-5", "claude-sonnet-5"]
        assert bounds.default_model_tier == "claude-opus-5"
        assert bounds.max_turn_cost_usd == "0.50"
        assert bounds.narrative_depths == ["summary", "analyst"]
        assert bounds.evidence_depths == ["standard", "deep"]
        assert bounds.evidence_depth_deep_multiplier > 1
        assert bounds.debug_available is True

    def test_no_tiers_are_offered_when_the_control_would_do_nothing(self) -> None:
        """A client rendering these bounds must not draw a tier picker
        against a model that cannot switch tiers."""
        bounds = SettingsPolicy.from_env(LIVE_ENV, model_tier_effective=False).bounds_payload()

        assert bounds.model_tiers == []
        assert bounds.model_tier_effective is False

    def test_an_invalid_ceiling_fails_at_construction(self) -> None:
        with pytest.raises(ValueError, match="REVI_LLM_MAX_BUDGET_USD"):
            SettingsPolicy.from_env({"REVI_LLM_MAX_BUDGET_USD": "free"})
