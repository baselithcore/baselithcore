"""Anthropic provider cloud backends: Bedrock and Vertex.

The Anthropic SDK ships native AWS Bedrock and GCP Vertex clients
(``AsyncAnthropicBedrock`` / ``AsyncAnthropicVertex``) that authenticate via
the cloud's own credential chain — no Anthropic API key. Selecting them via
``LLM_ANTHROPIC_BACKEND`` gives hyperscaler-native serving for Anthropic
models without an OpenAI-compatible gateway in between.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.services.llm.exceptions import LLMProviderError


@patch("core.services.llm.providers.anthropic_provider.anthropic")
def test_default_backend_builds_api_client_with_key(mock_anthropic):
    mock_anthropic.AsyncAnthropic.return_value = MagicMock()
    from core.services.llm.providers.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="sk-ant-test")
    provider._ensure_client()

    mock_anthropic.AsyncAnthropic.assert_called_once()
    assert mock_anthropic.AsyncAnthropic.call_args.kwargs["api_key"] == "sk-ant-test"


@patch("core.services.llm.providers.anthropic_provider.anthropic")
def test_bedrock_backend_builds_bedrock_client_without_api_key(mock_anthropic):
    mock_anthropic.AsyncAnthropicBedrock.return_value = MagicMock()
    from core.services.llm.providers.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(
        api_key=None, backend="bedrock", aws_region="eu-west-1"
    )
    provider._ensure_client()

    mock_anthropic.AsyncAnthropicBedrock.assert_called_once()
    assert (
        mock_anthropic.AsyncAnthropicBedrock.call_args.kwargs["aws_region"]
        == "eu-west-1"
    )
    mock_anthropic.AsyncAnthropic.assert_not_called()


@patch("core.services.llm.providers.anthropic_provider.anthropic")
def test_vertex_backend_builds_vertex_client(mock_anthropic):
    mock_anthropic.AsyncAnthropicVertex.return_value = MagicMock()
    from core.services.llm.providers.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(
        api_key=None,
        backend="vertex",
        vertex_project="my-project",
        vertex_region="europe-west4",
    )
    provider._ensure_client()

    kwargs = mock_anthropic.AsyncAnthropicVertex.call_args.kwargs
    assert kwargs["project_id"] == "my-project"
    assert kwargs["region"] == "europe-west4"


@patch("core.services.llm.providers.anthropic_provider.anthropic")
def test_api_backend_still_requires_key(mock_anthropic):
    from core.services.llm.providers.anthropic_provider import AnthropicProvider

    with pytest.raises(LLMProviderError, match="API key"):
        AnthropicProvider(api_key=None)


@patch("core.services.llm.providers.anthropic_provider.anthropic")
def test_unknown_backend_rejected(mock_anthropic):
    from core.services.llm.providers.anthropic_provider import AnthropicProvider

    with pytest.raises(LLMProviderError, match="backend"):
        AnthropicProvider(api_key=None, backend="azure")


@patch("core.services.llm.providers.anthropic_provider.anthropic")
def test_factory_forwards_backend_config(mock_anthropic):
    mock_anthropic.AsyncAnthropicBedrock.return_value = MagicMock()
    from core.services.llm.provider_factory import create_provider

    config = SimpleNamespace(
        provider="anthropic",
        api_key=None,
        anthropic_backend="bedrock",
        anthropic_aws_region="us-east-1",
        anthropic_vertex_project=None,
        anthropic_vertex_region=None,
        request_timeout=10.0,
        connect_timeout=2.0,
    )
    provider = create_provider(config)
    provider._ensure_client()

    assert (
        mock_anthropic.AsyncAnthropicBedrock.call_args.kwargs["aws_region"]
        == "us-east-1"
    )
