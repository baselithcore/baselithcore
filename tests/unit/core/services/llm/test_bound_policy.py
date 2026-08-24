"""The LLM funnel honours a policy bound to the execution context.

A queue worker hosts no plugins, so the per-plugin policy *resolver* is never
installed there. Without a bound-policy fallback, a plugin pinned to one
provider had its HTTP calls served by that provider and its background work by
the deployment default — the same plugin answering from two different models.
"""

from unittest.mock import patch

import pytest

from core.services.llm import policy as policy_mod
from core.services.llm.policy import PluginLLMPolicy, bind_llm_policy, reset_llm_policy


@pytest.fixture(autouse=True)
def _clean_context():
    token = bind_llm_policy(None)
    yield
    reset_llm_policy(token)


class TestBoundPolicyRoundTrip:
    def test_meta_round_trip(self):
        policy = PluginLLMPolicy(provider="ollama", model="llama3.2")
        assert policy_mod.policy_from_meta(policy_mod.policy_as_meta(policy)) == policy

    def test_empty_policy_carries_nothing(self):
        assert policy_mod.policy_as_meta(PluginLLMPolicy()) is None
        assert policy_mod.policy_as_meta(None) is None

    def test_unsupported_provider_is_dropped(self):
        assert policy_mod.policy_from_meta({"provider": "bogus"}) is None

    def test_malformed_meta_is_dropped(self):
        assert policy_mod.policy_from_meta(None) is None
        assert policy_mod.policy_from_meta({"provider": 42}) is None


class TestFunnelPrecedence:
    def test_bound_policy_serves_when_no_resolver_answers(self):
        from core.services.llm import runtime

        bound = PluginLLMPolicy(provider="ollama", model="llama3.2")
        bind_llm_policy(bound)

        with patch.object(
            runtime, "resolve_active_llm_policy", return_value=None
        ), patch.object(runtime, "_service_for_policy") as for_policy:
            runtime.get_llm_service()

        for_policy.assert_called_once_with(bound)

    def test_live_resolver_wins_over_the_bound_fallback(self):
        from core.services.llm import runtime

        resolved = PluginLLMPolicy(provider="openai", model="gpt-4o-mini")
        bind_llm_policy(PluginLLMPolicy(provider="ollama", model="llama3.2"))

        with patch.object(
            runtime, "resolve_active_llm_policy", return_value=resolved
        ), patch.object(runtime, "_service_for_policy") as for_policy:
            runtime.get_llm_service()

        for_policy.assert_called_once_with(resolved)

    def test_no_policy_at_all_serves_the_default(self):
        from core.services.llm import runtime

        with patch.object(
            runtime, "resolve_active_llm_policy", return_value=None
        ), patch.object(runtime, "_get_default_service") as default, patch.object(
            runtime, "_service_for_policy"
        ) as for_policy:
            runtime.get_llm_service()

        default.assert_called_once()
        for_policy.assert_not_called()
