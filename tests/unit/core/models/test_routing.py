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

# Verified 2026-09-13 against the official Anthropic model catalog (USD per 1M
# tokens, input/output). This is the exact table the pricing-completeness
# test below checks DEFAULT_PRICING against.
ANTHROPIC_PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5-1": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


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

    def test_negative_cache_tokens_rejected(self) -> None:
        with pytest.raises(ValueError):
            ModelPrice(1.0, 1.0).estimate(0, 0, cache_read_tokens=-1)
        with pytest.raises(ValueError):
            ModelPrice(1.0, 1.0).estimate(0, 0, cache_write_tokens=-1)


class TestCacheTierMath:
    def test_cache_read_defaults_to_one_tenth_of_input(self) -> None:
        p = ModelPrice(input_usd_per_million=10.0, output_usd_per_million=50.0)
        assert p.effective_cache_read_usd_per_million == pytest.approx(1.0)

    def test_cache_write_defaults_to_1_25x_input(self) -> None:
        p = ModelPrice(input_usd_per_million=10.0, output_usd_per_million=50.0)
        assert p.effective_cache_write_usd_per_million == pytest.approx(12.5)

    def test_explicit_cache_prices_override_the_derived_default(self) -> None:
        p = ModelPrice(
            input_usd_per_million=10.0,
            output_usd_per_million=50.0,
            cache_read_usd_per_million=2.0,
            cache_write_usd_per_million=20.0,
        )
        assert p.effective_cache_read_usd_per_million == pytest.approx(2.0)
        assert p.effective_cache_write_usd_per_million == pytest.approx(20.0)

    def test_estimate_charges_cache_tokens_at_the_derived_rates(self) -> None:
        p = ModelPrice(input_usd_per_million=10.0, output_usd_per_million=50.0)
        cost = p.estimate(
            0, 0, cache_read_tokens=1_000_000, cache_write_tokens=1_000_000
        )
        # 1.0 (cache read) + 12.5 (cache write)
        assert cost == pytest.approx(1.0 + 12.5)

    def test_cache_tokens_add_on_top_of_input_output(self) -> None:
        p = ModelPrice(input_usd_per_million=10.0, output_usd_per_million=50.0)
        base = p.estimate(1_000_000, 1_000_000)
        with_cache = p.estimate(
            1_000_000, 1_000_000, cache_read_tokens=1_000_000, cache_write_tokens=0
        )
        assert with_cache == pytest.approx(base + 1.0)


class TestBatchMultiplier:
    def test_batch_defaults_to_half_price(self) -> None:
        p = ModelPrice(input_usd_per_million=10.0, output_usd_per_million=50.0)
        full = p.estimate(1_000_000, 1_000_000)
        batched = p.estimate(1_000_000, 1_000_000, batch=True)
        assert batched == pytest.approx(full * 0.5)

    def test_custom_batch_multiplier(self) -> None:
        p = ModelPrice(
            input_usd_per_million=10.0,
            output_usd_per_million=50.0,
            batch_multiplier=0.25,
        )
        full = p.estimate(1_000_000, 1_000_000)
        batched = p.estimate(1_000_000, 1_000_000, batch=True)
        assert batched == pytest.approx(full * 0.25)

    def test_batch_false_is_a_noop(self) -> None:
        p = ModelPrice(input_usd_per_million=10.0, output_usd_per_million=50.0)
        assert p.estimate(1000, 1000, batch=False) == p.estimate(1000, 1000)


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

    def test_pricing_table_matches_global_constraints_exactly(self) -> None:
        """Every Anthropic model id in the Global Constraints is present with
        the exact verified price — the completeness test the brief requires."""
        for model_id, (input_price, output_price) in ANTHROPIC_PRICES.items():
            assert model_id in DEFAULT_PRICING, (
                f"{model_id} missing from DEFAULT_PRICING"
            )
            price = DEFAULT_PRICING[model_id]
            assert price.input_usd_per_million == pytest.approx(input_price), model_id
            assert price.output_usd_per_million == pytest.approx(output_price), model_id

    def test_estimate_cost_for_known_model(self) -> None:
        cost = estimate_cost("claude-haiku-4-5", 1_000_000, 1_000_000)
        haiku = DEFAULT_PRICING["claude-haiku-4-5"]
        assert cost == pytest.approx(
            haiku.input_usd_per_million + haiku.output_usd_per_million
        )

    def test_estimate_cost_threads_cache_and_batch_kwargs(self) -> None:
        haiku = DEFAULT_PRICING["claude-haiku-4-5"]
        cost = estimate_cost(
            "claude-haiku-4-5",
            0,
            0,
            cache_read_tokens=1_000_000,
            cache_write_tokens=0,
            batch=True,
        )
        expected = haiku.effective_cache_read_usd_per_million * 0.5
        assert cost == pytest.approx(expected)

    def test_custom_table_takes_precedence(self) -> None:
        custom = {"x": ModelPrice(0.5, 1.5)}
        assert get_price("x", table=custom) is custom["x"]


class TestModelRouter:
    def test_planning_routes_to_flagship(self) -> None:
        d = ModelRouter().select(TaskCategory.PLANNING)
        assert d.model_id == "claude-opus-5"
        assert d.rule == "primary"

    def test_reasoning_routes_to_flagship(self) -> None:
        d = ModelRouter().select(TaskCategory.REASONING)
        assert d.model_id == "claude-opus-5"

    def test_classification_routes_to_haiku_by_default(self) -> None:
        d = ModelRouter().select(TaskCategory.CLASSIFICATION)
        assert d.model_id == "claude-haiku-4-5"

    def test_summarization_and_embedding_route_to_haiku_by_default(self) -> None:
        assert (
            ModelRouter().select(TaskCategory.SUMMARIZATION).model_id
            == "claude-haiku-4-5"
        )
        assert (
            ModelRouter().select(TaskCategory.EMBEDDING).model_id == "claude-haiku-4-5"
        )

    def test_execution_routes_to_sonnet_by_default(self) -> None:
        d = ModelRouter().select(TaskCategory.EXECUTION)
        assert d.model_id == "claude-sonnet-5"

    def test_complex_execution_upgrades_to_opus(self) -> None:
        d = ModelRouter().select(TaskCategory.EXECUTION, complexity=Complexity.COMPLEX)
        assert d.model_id == "claude-opus-5"
        assert d.rule == "complexity_upgrade"

    def test_complex_summarization_and_classification_upgrade_to_sonnet(self) -> None:
        d1 = ModelRouter().select(
            TaskCategory.SUMMARIZATION, complexity=Complexity.COMPLEX
        )
        assert d1.model_id == "claude-sonnet-5"
        d2 = ModelRouter().select(
            TaskCategory.CLASSIFICATION, complexity=Complexity.COMPLEX
        )
        assert d2.model_id == "claude-sonnet-5"

    def test_simple_execution_stays_on_sonnet(self) -> None:
        d = ModelRouter().select(TaskCategory.EXECUTION, complexity=Complexity.SIMPLE)
        assert d.model_id == "claude-sonnet-5"

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


class TestMaxCostGuard:
    def test_noop_when_no_budget_hint_given(self) -> None:
        d = ModelRouter().select(TaskCategory.PLANNING)
        assert d.model_id == "claude-opus-5"

    def test_within_budget_keeps_the_policy_pick(self) -> None:
        d = ModelRouter().select(TaskCategory.PLANNING, max_cost_per_1k_usd=1.0)
        assert d.model_id == "claude-opus-5"
        assert d.rule == "primary"

    def test_over_budget_falls_back_to_a_cheaper_candidate(self) -> None:
        # opus-5 costs (5+25)/2000 = 0.015 per 1k (500in/500out); haiku-4-5
        # costs (1+5)/2000 = 0.003 per 1k — well under a 0.005 cap.
        d = ModelRouter().select(TaskCategory.PLANNING, max_cost_per_1k_usd=0.005)
        assert d.model_id == "claude-haiku-4-5"
        assert d.rule == "cost_guard"

    def test_over_budget_prefers_the_priciest_model_that_still_fits(self) -> None:
        # opus-5 = 0.015/1k (too expensive); sonnet-5 = 0.006/1k fits and is
        # pricier than haiku-4-5 (0.003/1k) — the guard must pick the best
        # affordable candidate, not just any/the globally cheapest one.
        d = ModelRouter().select(TaskCategory.PLANNING, max_cost_per_1k_usd=0.008)
        assert d.model_id == "claude-sonnet-5"
        assert d.rule == "cost_guard"

    def test_impossible_budget_returns_the_cheapest_candidate(self) -> None:
        d = ModelRouter().select(TaskCategory.PLANNING, max_cost_per_1k_usd=0.0000001)
        assert d.model_id == "claude-haiku-4-5"
        assert d.rule == "cost_guard"

    def test_guard_applies_after_complexity_upgrade(self) -> None:
        d = ModelRouter().select(
            TaskCategory.EXECUTION,
            complexity=Complexity.COMPLEX,
            max_cost_per_1k_usd=0.005,
        )
        # Complexity upgrade picks opus-5 (too expensive); guard downgrades it.
        assert d.model_id == "claude-haiku-4-5"
        assert d.rule == "cost_guard"
