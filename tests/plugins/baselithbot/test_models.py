"""Unit tests for Pydantic models in plugins.baselithbot.types."""

from __future__ import annotations

import pytest

from plugins.baselithbot.types import (
    BaselithbotResult,
    BaselithbotTask,
    StealthConfig,
)


class TestStealthConfig:
    def test_defaults_are_production_safe(self) -> None:
        cfg = StealthConfig()
        assert cfg.enabled is True
        assert cfg.rotate_user_agent is True
        assert cfg.mask_webdriver is True
        assert cfg.spoof_languages == ["en-US", "en"]
        assert cfg.spoof_timezone == "UTC"
        assert len(cfg.user_agents) >= 3

    def test_user_agents_accept_override(self) -> None:
        cfg = StealthConfig(user_agents=["custom-agent"])
        assert cfg.user_agents == ["custom-agent"]


class TestBaselithbotTask:
    def test_goal_is_required(self) -> None:
        with pytest.raises(Exception):
            BaselithbotTask()  # type: ignore[call-arg]

    def test_max_steps_bounds(self) -> None:
        BaselithbotTask(goal="x", max_steps=1)
        BaselithbotTask(goal="x", max_steps=100)
        with pytest.raises(Exception):
            BaselithbotTask(goal="x", max_steps=0)
        with pytest.raises(Exception):
            BaselithbotTask(goal="x", max_steps=101)

    def test_default_extract_fields(self) -> None:
        task = BaselithbotTask(goal="collect prices")
        assert task.extract_fields == []
        assert task.start_url is None


class TestBaselithbotResult:
    def test_minimum_fields(self) -> None:
        result = BaselithbotResult(success=True, final_url="https://x", steps_taken=3)
        assert result.success is True
        assert result.tokens_used == 0
        assert result.extracted_data == {}
        assert result.history == []
        assert result.model is None
        assert result.provider is None
