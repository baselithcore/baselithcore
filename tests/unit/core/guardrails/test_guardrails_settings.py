"""``GUARDRAILS_*`` settings reach the guards the app runs.

Regression: ``GuardrailsConfig`` was a dataclass nothing built from the
environment, so ``allowed_topics`` and ``custom_block_patterns`` could not be
set in the running app and the ``out_of_scope`` verdict could never fire.
"""

from __future__ import annotations

import pytest

import core.config.guardrails as guardrails_settings
from core.guardrails.moderation import get_guardrails_config
from core.orchestration import guard_pipeline


@pytest.fixture
def fresh_settings(monkeypatch):
    monkeypatch.setattr(guardrails_settings, "_guardrails_settings", None)
    guard_pipeline._guards.cache_clear()
    yield
    monkeypatch.setattr(guardrails_settings, "_guardrails_settings", None)
    guard_pipeline._guards.cache_clear()


def test_defaults_match_the_dataclass(fresh_settings, monkeypatch):
    from core.guardrails.config import GuardrailsConfig

    for name in ("GUARDRAILS_ALLOWED_TOPICS", "GUARDRAILS_CUSTOM_BLOCK_PATTERNS"):
        monkeypatch.delenv(name, raising=False)
    assert get_guardrails_config() == GuardrailsConfig()


def test_environment_configures_the_runtime_guards(fresh_settings, monkeypatch):
    monkeypatch.setenv("GUARDRAILS_ALLOWED_TOPICS", "internal HR policies")
    monkeypatch.setenv("GUARDRAILS_CUSTOM_BLOCK_PATTERNS", r"project\s+falcon,codename")
    monkeypatch.setenv("GUARDRAILS_MAX_INPUT_LENGTH", "50")

    config = get_guardrails_config()
    assert config.allowed_topics == "internal HR policies"
    assert config.custom_block_patterns == [r"project\s+falcon", "codename"]

    input_guard, _ = guard_pipeline._guards()
    assert input_guard.config.allowed_topics == "internal HR policies"
    assert not input_guard.validate("tell me about Project  Falcon").is_valid
    assert not input_guard.validate("x" * 51).is_valid
    assert input_guard.validate("what is the leave policy?").is_valid
