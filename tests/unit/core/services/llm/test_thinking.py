"""Unit tests for extended-thinking / reasoning-effort budgets."""

import pytest

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


class TestEffortForCategory:
    def test_default_mapping(self):
        from core.services.llm.thinking import EffortLevel, effort_for_category

        assert effort_for_category("planning") is EffortLevel.HIGH
        assert effort_for_category("reasoning") is EffortLevel.HIGH
        assert effort_for_category("execution") is EffortLevel.MEDIUM
        assert effort_for_category("summarization") is EffortLevel.LOW
        assert effort_for_category("classification") is EffortLevel.OFF
        assert effort_for_category("embedding") is EffortLevel.OFF

    def test_unknown_or_missing_category(self):
        from core.services.llm.thinking import effort_for_category

        assert effort_for_category(None) is None
        assert effort_for_category("") is None
        assert effort_for_category("nonsense") is None

    def test_case_and_whitespace_insensitive(self):
        from core.services.llm.thinking import EffortLevel, effort_for_category

        assert effort_for_category("  Planning ") is EffortLevel.HIGH


class TestExtendedEffortTiers:
    """``xhigh``/``max`` exist on the newest families."""

    def test_new_tiers_are_declared(self):
        assert EffortLevel.XHIGH.value == "xhigh"
        assert EffortLevel.MAX.value == "max"

    def test_new_tiers_have_larger_budgets(self):
        assert budget_for_effort(EffortLevel.XHIGH) > budget_for_effort(
            EffortLevel.HIGH
        )
        assert budget_for_effort(EffortLevel.MAX) > budget_for_effort(EffortLevel.XHIGH)

    def test_xhigh_string_is_coerced(self):
        plan = resolve_thinking(effort="xhigh")
        assert plan.enabled is True
        assert plan.effort is EffortLevel.XHIGH


class TestAdaptiveKwargs:
    """Modern families take ``thinking: {"type": "adaptive"}`` and no sampling."""

    def test_adaptive_family_gets_adaptive_thinking_and_effort(self):
        plan = resolve_thinking(effort="high", max_tokens=16000)
        kwargs = plan.to_kwargs("claude-opus-5")
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["output_config"] == {"effort": "high"}
        # budget_tokens is a 400 on this family.
        assert "budget_tokens" not in kwargs["thinking"]
        # So is temperature.
        assert "temperature" not in kwargs

    def test_xhigh_survives_on_a_family_that_supports_it(self):
        kwargs = resolve_thinking(effort="xhigh").to_kwargs("claude-sonnet-5")
        assert kwargs["output_config"] == {"effort": "xhigh"}

    def test_xhigh_is_clamped_where_unsupported(self):
        kwargs = resolve_thinking(effort="xhigh").to_kwargs("claude-opus-4-6")
        assert kwargs["output_config"] == {"effort": "high"}

    def test_max_is_clamped_where_unsupported(self):
        kwargs = resolve_thinking(effort="max").to_kwargs("claude-sonnet-4-6")
        assert kwargs["output_config"] == {"effort": "high"}

    def test_explicit_budget_on_an_adaptive_family_sends_no_budget_tokens(self):
        # A caller's legacy thinking_budget must not produce a 400.
        kwargs = resolve_thinking(thinking_budget=5000).to_kwargs("claude-opus-4-8")
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert "output_config" not in kwargs

    def test_disabled_plan_sends_no_thinking_key(self):
        kwargs = resolve_thinking(effort="off", max_tokens=2048).to_kwargs(
            "claude-opus-5"
        )
        assert kwargs == {"max_tokens": 2048}


class TestBudgetKwargs:
    """Older families still require the ``enabled`` + ``budget_tokens`` form."""

    def test_budget_family_keeps_the_legacy_shape(self):
        kwargs = resolve_thinking(effort="high", max_tokens=4096).to_kwargs(
            "claude-haiku-4-5"
        )
        assert kwargs["thinking"]["type"] == "enabled"
        assert kwargs["thinking"]["budget_tokens"] == budget_for_effort(
            EffortLevel.HIGH
        )
        # The budget form requires a neutral temperature.
        assert kwargs["temperature"] == 1.0
        assert kwargs["max_tokens"] > kwargs["thinking"]["budget_tokens"]

    def test_unknown_model_keeps_the_legacy_shape(self):
        kwargs = resolve_thinking(effort="low").to_kwargs("claude-3-5-sonnet-20240620")
        assert kwargs["thinking"]["type"] == "enabled"

    def test_legacy_alias_still_works(self):
        # to_anthropic_kwargs predates the capability table; callers keep it.
        plan = resolve_thinking(thinking_budget=5000, max_tokens=2000)
        assert plan.to_anthropic_kwargs() == plan.to_kwargs("claude-haiku-4-5")


class TestOutputCapClamping:
    """A request must not exceed a ceiling we actually know the family has."""

    def test_a_listed_family_is_clamped_to_its_ceiling(self):
        kwargs = resolve_thinking(max_tokens=500000).to_kwargs("claude-opus-5")
        assert kwargs["max_tokens"] == 128000

    def test_a_smaller_listed_ceiling_also_applies(self):
        kwargs = resolve_thinking(max_tokens=100000).to_kwargs("claude-haiku-4-5")
        assert kwargs["max_tokens"] == 64000

    def test_the_budget_shrinks_with_a_clamped_cap(self):
        # The API requires budget_tokens < max_tokens; clamping the cap without
        # the budget would make the request invalid.
        kwargs = resolve_thinking(thinking_budget=70000, max_tokens=4096).to_kwargs(
            "claude-haiku-4-5"
        )
        assert kwargs["max_tokens"] == 64000
        assert kwargs["thinking"]["budget_tokens"] < kwargs["max_tokens"]

    def test_a_request_under_the_ceiling_is_untouched(self):
        kwargs = resolve_thinking(effort="high", max_tokens=100000).to_kwargs(
            "claude-opus-5"
        )
        assert kwargs["max_tokens"] == 100000

    def test_thinking_is_dropped_when_no_budget_fits_the_ceiling(self):
        from unittest.mock import patch

        from core.services.llm.model_capabilities import (
            _TABLE,
            ModelCapabilities,
            ThinkingMode,
        )

        tiny = ModelCapabilities(
            thinking_mode=ThinkingMode.BUDGET,
            supports_effort_levels=frozenset(),
            max_output_tokens=1500,
        )
        with patch.dict(_TABLE, {"claude-tiny-9": tiny}):
            kwargs = resolve_thinking(thinking_budget=8000).to_kwargs("claude-tiny-9")
        # No budget >= the API minimum fits under 1500, so the request goes out
        # without a scratchpad rather than being refused.
        assert kwargs == {"max_tokens": 1500}


class TestUnlistedModelsAreNotClamped:
    """The fallback profile is a guess — it must not truncate a real request.

    Plenty of current ids resolve to it (Bedrock/Vertex spellings, ``-latest``
    aliases, gateway names, any model released after this table was written),
    and every one of them supports far more than the legacy ceiling.
    """

    @pytest.mark.parametrize(
        "model",
        [
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "claude-sonnet-4-5",
            "claude-opus-4-1",
            "claude-3-7-sonnet-latest",
            "my-gateway/claude-sonnet-4-5",
        ],
    )
    def test_an_unlisted_id_keeps_the_callers_max_tokens(self, model):
        assert resolve_thinking(max_tokens=32000).to_kwargs(model)["max_tokens"] == (
            32000
        )

    def test_an_unlisted_id_keeps_its_grown_thinking_budget(self):
        # ``claude-sonnet-4-5`` is unlisted and supports far more than the
        # fallback floor, so nothing here may be trimmed on a guess.
        kwargs = resolve_thinking(effort="high", max_tokens=4096).to_kwargs(
            "claude-sonnet-4-5"
        )
        assert kwargs["max_tokens"] == 13024
        assert kwargs["thinking"]["budget_tokens"] == 12000

    def test_a_listed_legacy_id_is_clamped_to_its_real_ceiling(self):
        # 3.5 has a row, so its 8192 ceiling is enforceable: the grown budget
        # is trimmed to fit instead of being refused.
        kwargs = resolve_thinking(effort="high", max_tokens=4096).to_kwargs(
            "claude-3-5-sonnet-20240620"
        )
        assert kwargs["max_tokens"] == 8192
        assert kwargs["thinking"]["budget_tokens"] < kwargs["max_tokens"]
