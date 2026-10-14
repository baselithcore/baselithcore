"""Provider-level default ``reasoning_effort`` for OpenAI requests."""

from baselith.core.config.services import LLMConfig
from baselith.core.services.llm.providers._openai_request import request_kwargs
from baselith.core.services.llm.providers.openai_provider import OpenAIProvider


def test_default_effort_is_sent_when_configured() -> None:
    assert request_kwargs({}, "none") == {"reasoning_effort": "none"}


def test_nothing_is_sent_without_a_default() -> None:
    assert "reasoning_effort" not in request_kwargs({"temperature": 0.2})


def test_caller_effort_wins_over_default() -> None:
    out = request_kwargs({"reasoning_effort": "high"}, "none")
    assert out["reasoning_effort"] == "high"


def test_cross_provider_effort_hint_is_not_forwarded() -> None:
    assert request_kwargs({"effort": "high"}, "none") == {"reasoning_effort": "none"}


def test_config_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_OPENAI_REASONING_EFFORT", "none")
    assert LLMConfig().openai_reasoning_effort == "none"


def test_config_blank_env_means_unset(monkeypatch) -> None:
    monkeypatch.setenv("LLM_OPENAI_REASONING_EFFORT", "")
    assert LLMConfig().openai_reasoning_effort is None


def test_factory_passes_effort_to_provider(monkeypatch) -> None:
    from baselith.core.services.llm.provider_factory import create_provider

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_OPENAI_REASONING_EFFORT", "none")
    provider = create_provider(LLMConfig())
    assert isinstance(provider, OpenAIProvider)
    assert provider._reasoning_effort == "none"
