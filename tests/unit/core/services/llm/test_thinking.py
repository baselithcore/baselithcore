"""Unit tests for extended-thinking / reasoning-effort budgets."""

from core.services.llm.thinking import (
    EffortLevel,
    budget_for_effort,
    resolve_thinking,
)


def test_no_args_disables_thinking():
    plan = resolve_thinking()
    assert plan.enabled is False
    assert plan.budget_tokens == 0
    assert plan.to_anthropic_kwargs() == {"max_tokens": 4096}


def test_off_effort_disables_thinking():
    plan = resolve_thinking(effort="off", max_tokens=2048)
    assert plan.enabled is False
    assert plan.max_tokens == 2048


def test_high_effort_uses_sweet_spot_budget():
    plan = resolve_thinking(effort=EffortLevel.HIGH, max_tokens=4096)
    assert plan.enabled is True
    assert plan.budget_tokens == budget_for_effort(EffortLevel.HIGH)
    # max_tokens grows to leave answer head-room above the thinking budget.
    assert plan.max_tokens > plan.budget_tokens


def test_string_effort_is_coerced():
    plan = resolve_thinking(effort="medium")
    assert plan.enabled is True
    assert plan.budget_tokens == budget_for_effort(EffortLevel.MEDIUM)


def test_unknown_effort_falls_back_to_off():
    plan = resolve_thinking(effort="turbo")
    assert plan.enabled is False


def test_explicit_budget_overrides_effort():
    plan = resolve_thinking(effort="low", thinking_budget=9000, max_tokens=1000)
    assert plan.enabled is True
    assert plan.budget_tokens == 9000
    assert plan.max_tokens > 9000


def test_anthropic_kwargs_shape_when_enabled():
    plan = resolve_thinking(thinking_budget=5000, max_tokens=2000)
    kw = plan.to_anthropic_kwargs()
    assert kw["temperature"] == 1.0
    assert kw["thinking"] == {"type": "enabled", "budget_tokens": 5000}
    assert kw["max_tokens"] > 5000


def test_zero_budget_disables():
    plan = resolve_thinking(thinking_budget=0)
    assert plan.enabled is False
