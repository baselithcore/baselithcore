"""Unit tests for the cost-guard passthrough in ``core.services.llm.model_routing``.

``routed_model`` resolves a router decision for LLMService; these tests cover
only the new ``routing_max_cost_per_1k_usd`` duck-typed config hint, which is
read via ``getattr`` so it works whether or not ``LLMConfig`` (owned by a
different task) declares the attribute yet.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.services.llm.model_routing import routed_model


def _config(**overrides: object) -> SimpleNamespace:
    base = {"routing_enabled": True, "routing_policy": ""}
    base.update(overrides)
    return SimpleNamespace(**base)


class TestCostGuardPassthrough:
    def test_no_hint_attribute_behaves_exactly_as_before(self) -> None:
        config = _config()
        assert routed_model(config, "planning") == "claude-opus-5"

    def test_hint_none_behaves_exactly_as_before(self) -> None:
        config = _config(routing_max_cost_per_1k_usd=None)
        assert routed_model(config, "planning") == "claude-opus-5"

    def test_hint_downgrades_to_a_cheaper_model(self) -> None:
        config = _config(routing_max_cost_per_1k_usd=0.005)
        assert routed_model(config, "planning") == "claude-haiku-4-5"

    def test_hint_is_a_noop_when_the_pick_already_fits(self) -> None:
        config = _config(routing_max_cost_per_1k_usd=1.0)
        assert routed_model(config, "planning") == "claude-opus-5"
