"""Unit tests for ``core.models.routing`` and ``core.models.pricing``."""

from __future__ import annotations

import pytest

from core.models.pricing import (
    DEFAULT_PRICING,
    UNKNOWN_PRICE,
    ModelPrice,
    estimate_cost,
    get_price,
)
from core.models.routing import (
    Complexity,
    ModelRouter,
    RoutingPolicy,
    TaskCategory,
)


class TestModelPrice:
    def test_estimate_with_typical_call(self) -> None:
        p = ModelPrice(input_usd_per_million=3.0, output_usd_per_million=15.0)
        cost = p.estimate(prompt_tokens=1_000, completion_tokens=500)
        assert cost == pytest.approx(0.003 + 0.0075)

    def test_zero_tokens_zero_cost(self) -> None:
        p = ModelPrice(1.0, 1.0)
        assert p.estimate(0, 0) == 0.0

    def test_negative_tokens_rejected(self) -> None:
        with pytest.raises(ValueError):
            ModelPrice(1.0, 1.0).estimate(-1, 0)


class TestPricingTable:
    def test_unknown_model_falls_back_to_high_price(self) -> None:
        price = get_price("nonexistent-model")
        assert price is UNKNOWN_PRICE
        assert price.input_usd_per_million >= 50.0

    def test_default_table_contains_flagship_models(self) -> None:
        assert "claude-opus-4-8" in DEFAULT_PRICING
        assert "claude-opus-4-7" in DEFAULT_PRICING  # still served
        assert "claude-fable-5" in DEFAULT_PRICING
        assert "claude-sonnet-5" in DEFAULT_PRICING
        assert "claude-sonnet-4-6" in DEFAULT_PRICING
        assert "gpt-4o-mini" in DEFAULT_PRICING

    def test_estimate_cost_for_known_model(self) -> None:
        cost = estimate_cost("claude-haiku-4-5", 1_000_000, 1_000_000)
        haiku = DEFAULT_PRICING["claude-haiku-4-5"]
        assert cost == pytest.approx(
            haiku.input_usd_per_million + haiku.output_usd_per_million
        )

    def test_custom_table_takes_precedence(self) -> None:
        custom = {"x": ModelPrice(0.5, 1.5)}
        assert get_price("x", table=custom) is custom["x"]


class TestModelRouter:
    def test_planning_routes_to_flagship(self) -> None:
        d = ModelRouter().select(TaskCategory.PLANNING)
        assert d.model_id == "claude-opus-4-8"
        assert d.rule == "primary"

    def test_classification_routes_to_haiku_by_default(self) -> None:
        d = ModelRouter().select(TaskCategory.CLASSIFICATION)
        assert d.model_id == "claude-haiku-4-5"

    def test_complex_execution_upgrades_to_opus(self) -> None:
        d = ModelRouter().select(TaskCategory.EXECUTION, complexity=Complexity.COMPLEX)
        assert d.model_id == "claude-opus-4-8"
        assert d.rule == "complexity_upgrade"

    def test_simple_execution_stays_on_sonnet(self) -> None:
        d = ModelRouter().select(TaskCategory.EXECUTION, complexity=Complexity.SIMPLE)
        assert d.model_id == "claude-sonnet-4-6"

    def test_decision_carries_signal(self) -> None:
        d = ModelRouter().select(TaskCategory.REASONING, complexity=Complexity.MEDIUM)
        assert d.category is TaskCategory.REASONING
        assert d.complexity is Complexity.MEDIUM

    def test_custom_policy_overrides(self) -> None:
        policy = RoutingPolicy(primary={TaskCategory.PLANNING: "gpt-5"})
        d = ModelRouter(policy=policy).select(TaskCategory.PLANNING)
        assert d.model_id == "gpt-5"

    def test_missing_primary_raises(self) -> None:
        policy = RoutingPolicy(primary={})
        with pytest.raises(KeyError):
            ModelRouter(policy=policy).select(TaskCategory.REASONING)
