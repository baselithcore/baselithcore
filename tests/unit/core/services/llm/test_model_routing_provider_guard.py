"""Routing must not hand a provider a model from another vendor.

The built-in policy maps every task category onto a Claude model id. Nothing
checked that the configured provider actually serves those ids, so turning
``routing_enabled`` on in an OpenAI or Ollama deployment made every categorized
call ask for a model that provider has never heard of — a 404 on each request,
for a feature advertised as a cost optimization.

Routing is documented as a *hint*: resolving to ``None`` falls back to the
configured default model. An unservable pick is exactly that case, so the guard
returns ``None`` rather than raising.

The guard blocks only a **certain** mismatch — a recognizable family served by
a different provider. An unrecognizable id (every local tag: ``llama3.2``,
``qwen2.5-coder``) is the operator's own choice and passes through, as does a
config that names no provider at all.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.services.llm.model_routing import routed_model

pytestmark = pytest.mark.unit


def _config(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {"routing_enabled": True, "routing_policy": ""}
    base.update(overrides)
    return SimpleNamespace(**base)


class TestDefaultPolicyAgainstForeignProviders:
    """The built-in policy is all Claude ids; only Claude providers may use it."""

    @pytest.mark.parametrize("provider", ["openai", "gemini", "ollama", "huggingface"])
    def test_foreign_provider_falls_back_instead_of_asking_for_claude(
        self, provider: str
    ) -> None:
        config = _config(provider=provider)
        assert routed_model(config, "planning") is None

    @pytest.mark.parametrize("provider", ["anthropic", "bedrock", "vertex"])
    def test_a_provider_that_serves_claude_still_routes(self, provider: str) -> None:
        """``LLMConfig.provider`` only spells the first of these — Anthropic
        behind Bedrock or Vertex is ``provider="anthropic"`` plus a backend.
        The reseller names are covered for the duck-typed configs this module
        is also called with."""
        config = _config(provider=provider)
        assert routed_model(config, "planning") == "claude-opus-5"

    def test_a_config_naming_no_provider_is_unaffected(self) -> None:
        """Duck-typed configs (and older ones) must keep their behaviour."""
        assert routed_model(_config(), "planning") == "claude-opus-5"


class TestExplicitPolicies:
    """An operator-supplied policy is respected wherever it is not provably wrong."""

    def test_local_tags_pass_through_on_a_local_provider(self) -> None:
        config = _config(
            provider="ollama",
            routing_policy='{"planning": "qwen2.5-coder", "execution": "llama3.2"}',
        )
        assert routed_model(config, "planning") == "qwen2.5-coder"
        assert routed_model(config, "execution") == "llama3.2"

    def test_openai_policy_routes_on_openai(self) -> None:
        config = _config(provider="openai", routing_policy='{"planning": "gpt-5"}')
        assert routed_model(config, "planning") == "gpt-5"

    def test_an_explicit_policy_is_still_checked_against_the_provider(self) -> None:
        """A copy-pasted Claude policy on OpenAI is a mistake, not an override."""
        config = _config(
            provider="openai", routing_policy='{"planning": "claude-opus-5"}'
        )
        assert routed_model(config, "planning") is None

    def test_gemini_policy_routes_on_vertex(self) -> None:
        """Vertex serves both Claude and Gemini ids."""
        config = _config(
            provider="vertex", routing_policy='{"planning": "gemini-2.5-pro"}'
        )
        assert routed_model(config, "planning") == "gemini-2.5-pro"


class TestUnchangedBehaviour:
    """The guard must not disturb the paths that already resolved to None."""

    def test_routing_disabled_still_returns_none(self) -> None:
        assert (
            routed_model(
                _config(routing_enabled=False, provider="anthropic"), "planning"
            )
            is None
        )

    def test_unknown_category_still_returns_none(self) -> None:
        assert routed_model(_config(provider="anthropic"), "nonsense") is None

    def test_cost_guard_substitution_is_still_checked(self) -> None:
        """A cost-guard substitution must be validated like any other pick."""
        config = _config(provider="anthropic", routing_max_cost_per_1k_usd=0.01)
        # Every candidate is Claude, so the substitution stays servable: the
        # guard passes the priciest model the budget still affords.
        assert routed_model(config, "planning") == "claude-sonnet-5"
