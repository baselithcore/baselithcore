"""OpenAI provider must honor a custom base URL.

Without it the provider can only reach api.openai.com — no Azure OpenAI, no
vLLM/LiteLLM/OpenRouter gateway, no OpenAI-compatible self-hosted serving.
``LLMConfig.api_base`` already carries the endpoint for the default provider;
the factory must forward it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import SecretStr


@patch("core.services.llm.providers.openai_provider.openai")
def test_provider_passes_base_url_to_sdk_client(mock_openai):
    mock_openai.AsyncOpenAI.return_value = MagicMock()
    from core.services.llm.providers.openai_provider import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test", base_url="http://gw.local/v1")
    provider._ensure_client()

    call_kwargs = mock_openai.AsyncOpenAI.call_args.kwargs
    assert call_kwargs["base_url"] == "http://gw.local/v1"


@patch("core.services.llm.providers.openai_provider.openai")
def test_provider_omits_base_url_by_default(mock_openai):
    mock_openai.AsyncOpenAI.return_value = MagicMock()
    from core.services.llm.providers.openai_provider import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test")
    provider._ensure_client()

    call_kwargs = mock_openai.AsyncOpenAI.call_args.kwargs
    assert "base_url" not in call_kwargs


@patch("core.services.llm.providers.openai_provider.openai")
def test_factory_forwards_api_base_for_openai(mock_openai):
    mock_openai.AsyncOpenAI.return_value = MagicMock()
    from core.services.llm.provider_factory import create_provider

    config = SimpleNamespace(
        provider="openai",
        api_key=SecretStr("sk-test"),
        api_base="http://gw.local/v1",
        request_timeout=10.0,
        connect_timeout=2.0,
    )
    provider = create_provider(config)
    provider._ensure_client()

    call_kwargs = mock_openai.AsyncOpenAI.call_args.kwargs
    assert call_kwargs["base_url"] == "http://gw.local/v1"
