"""Production-safe defaults for the agent loop's runtime caps.

``LoopLimits`` used to ship with ``max_tokens=None`` and ``max_seconds=None``:
a loop had no token ceiling and no wall-clock deadline unless a caller opted
in, and ``token_pressure()`` therefore always returned 0.0 — the context
auto-tuning that polls it was unreachable. These pin the settings-backed
defaults that close that gap.
"""

from __future__ import annotations

import pytest

from core.config.orchestration import OrchestrationConfig
from core.orchestration.limits import (
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    DEFAULT_MAX_SECONDS,
    DEFAULT_MAX_TOKENS,
    LoopBudget,
    LoopLimits,
)

pytestmark = [pytest.mark.unit]


class TestSettingsBackedDefaults:
    def test_config_exposes_loop_caps(self) -> None:
        config = OrchestrationConfig()
        assert config.loop_max_tokens == 400_000
        assert config.loop_max_seconds == 600.0
        assert config.context_window_tokens == 200_000
        assert config.recovery_sweep_interval_seconds == 300.0

    def test_limits_default_to_the_configured_caps(self) -> None:
        limits = LoopLimits()
        assert limits.max_tokens == DEFAULT_MAX_TOKENS == 400_000
        assert limits.max_seconds == DEFAULT_MAX_SECONDS == 600.0

    def test_explicit_none_still_disables_each_cap(self) -> None:
        limits = LoopLimits(max_tokens=None, max_seconds=None)
        assert limits.max_tokens is None
        assert limits.max_seconds is None
        assert LoopBudget(limits=limits).remaining_seconds() is None

    def test_zero_setting_disables_the_cap(self, monkeypatch) -> None:
        import core.config.orchestration as config_module
        import core.orchestration.limits as limits_module

        monkeypatch.setattr(config_module, "_orchestration_config", None)
        monkeypatch.setenv("ORCHESTRATOR_LOOP_MAX_TOKENS", "0")
        monkeypatch.setenv("ORCHESTRATOR_LOOP_MAX_SECONDS", "0")
        try:
            assert limits_module._default_max_tokens() is None
            assert limits_module._default_max_seconds() is None
        finally:
            config_module._orchestration_config = None

    def test_default_budget_has_a_wall_clock_deadline(self) -> None:
        budget = LoopBudget()
        remaining = budget.remaining_seconds()
        assert remaining is not None
        assert 0.0 < remaining <= DEFAULT_MAX_SECONDS


class TestTokenPressureFallback:
    def test_pressure_uses_the_token_cap_when_one_is_set(self) -> None:
        budget = LoopBudget(limits=LoopLimits(max_tokens=100))
        budget.record_tokens(40)
        assert budget.token_pressure() == pytest.approx(0.4)

    def test_pressure_falls_back_to_context_window_without_a_cap(self) -> None:
        budget = LoopBudget(limits=LoopLimits(max_tokens=None))
        budget.record_context_tokens(DEFAULT_CONTEXT_WINDOW_TOKENS // 4)
        assert budget.token_pressure() == pytest.approx(0.25)

    def test_fallback_pressure_is_clamped(self) -> None:
        budget = LoopBudget(limits=LoopLimits(max_tokens=None))
        budget.record_context_tokens(DEFAULT_CONTEXT_WINDOW_TOKENS * 5)
        assert budget.token_pressure() == 1.0

    def test_fallback_is_zero_without_any_context(self) -> None:
        assert LoopBudget(limits=LoopLimits(max_tokens=None)).token_pressure() == 0.0
