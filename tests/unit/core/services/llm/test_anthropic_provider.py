"""
Tests for core/services/llm/providers/anthropic_provider.py
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAnthropicProviderInit:
    """Tests for AnthropicProvider initialization."""

    @patch("core.services.llm.providers.anthropic_provider.anthropic")
    def test_init_with_api_key(self, mock_anthropic):
        """Verify provider initializes with API key."""
        from core.services.llm.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(api_key="sk-ant-test-key")
        # Force initialization
        provider._ensure_client()

        # Client is constructed with the key plus hardened defaults:
        # max_retries=0 (LLMService owns retries) and an explicit timeout.
        mock_anthropic.AsyncAnthropic.assert_called_once()
        call_kwargs = mock_anthropic.AsyncAnthropic.call_args.kwargs
        assert call_kwargs["api_key"] == "sk-ant-test-key"
        assert call_kwargs["max_retries"] == 0
        assert "timeout" in call_kwargs
        assert provider.client is not None

    def test_init_without_api_key_raises(self):
        """Verify provider raises error without API key."""
        from core.services.llm.exceptions import LLMProviderError
        from core.services.llm.providers.anthropic_provider import AnthropicProvider

        with pytest.raises(LLMProviderError) as exc_info:
            AnthropicProvider(api_key="")

        assert "API key is required" in str(exc_info.value)


@pytest.mark.asyncio
class TestAnthropicProviderGenerate:
    """Tests for AnthropicProvider.generate method."""

    async def test_generate_returns_content_and_tokens(self):
        """Verify generate returns tuple of content and tokens."""
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock()
        mock_response = MagicMock()

        # Mock content block
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "Hello from Claude!"
        mock_response.content = [mock_block]

        # Mock usage (cache counters default to 0 = no cache hit on this call)
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 15
        mock_response.usage.cache_creation_input_tokens = 0
        mock_response.usage.cache_read_input_tokens = 0

        mock_client.messages.create.return_value = mock_response

        with patch(
            "core.services.llm.providers.anthropic_provider.anthropic"
        ) as mock_anthropic:
            mock_anthropic.AsyncAnthropic.return_value = mock_client
            from core.services.llm.providers.anthropic_provider import AnthropicProvider

            provider = AnthropicProvider(api_key="sk-ant-test")
            content, tokens = await provider.generate(
                "Test prompt", model="claude-3-sonnet"
            )

            assert content == "Hello from Claude!"
            assert tokens == 25

    async def test_generate_with_json_mode(self):
        """Verify generate handles json_mode via system prompt or instructions."""
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock()
        mock_response = MagicMock()
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = '{"key": "value"}'
        mock_response.content = [mock_block]
        mock_response.usage.input_tokens = 5
        mock_response.usage.output_tokens = 5
        mock_client.messages.create.return_value = mock_response

        with patch(
            "core.services.llm.providers.anthropic_provider.anthropic"
        ) as mock_anthropic:
            mock_anthropic.AsyncAnthropic.return_value = mock_client
            from core.services.llm.providers.anthropic_provider import AnthropicProvider

            provider = AnthropicProvider(api_key="sk-ant-test")
            await provider.generate("Prompt", model="claude-3-sonnet", json_mode=True)

            call_kwargs = mock_client.messages.create.call_args[1]
            assert "JSON" in call_kwargs.get("system", "")


# Helper for async iteration
class AsyncIterator:
    def __init__(self, seq):
        self.iter = iter(seq)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.iter)
        except StopIteration:
            raise StopAsyncIteration


@pytest.mark.asyncio
class TestAnthropicProviderGenerateStream:
    """Tests for AnthropicProvider.generate_stream method."""

    async def test_generate_stream_yields_chunks(self):
        """Verify generate_stream yields content chunks."""
        mock_client = MagicMock()

        # Mock stream events
        # Anthropic 'text_delta' events have 'text' directly on the chunk
        mock_chunk1 = MagicMock()
        mock_chunk1.type = "text_delta"
        mock_chunk1.text = "Hello"

        mock_chunk2 = MagicMock()
        mock_chunk2.type = "text_delta"
        mock_chunk2.text = " world"

        # The stream itself is an async iterator
        mock_stream = AsyncIterator([mock_chunk1, mock_chunk2])

        # In anthropic-python, client.messages.stream() returns a context manager
        # that yields the stream.
        mock_manager = MagicMock()
        mock_manager.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_manager.__aexit__ = AsyncMock()

        mock_client.messages.stream.return_value = mock_manager

        with patch(
            "core.services.llm.providers.anthropic_provider.anthropic"
        ) as mock_anthropic:
            mock_anthropic.AsyncAnthropic.return_value = mock_client
            mock_anthropic.NOT_GIVEN = "not_given"

            from core.services.llm.providers.anthropic_provider import AnthropicProvider

            provider = AnthropicProvider(api_key="sk-ant-test")
            chunks = []
            async for chunk_text, _ in provider.generate_stream(
                "Prompt", model="claude-3-sonnet"
            ):
                chunks.append(chunk_text)

            assert len(chunks) == 2
            assert chunks[0] == "Hello"
            assert chunks[1] == " world"


@pytest.mark.asyncio
class TestAnthropicProviderPromptCache:
    """Prompt caching: the stable system prefix carries an ephemeral breakpoint."""

    @staticmethod
    def _mock_client():
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock()
        mock_response = MagicMock()
        block = MagicMock()
        block.type = "text"
        block.text = "ok"
        mock_response.content = [block]
        mock_response.usage.input_tokens = 5
        mock_response.usage.output_tokens = 5
        mock_response.usage.cache_creation_input_tokens = 0
        mock_response.usage.cache_read_input_tokens = 0
        mock_client.messages.create.return_value = mock_response
        return mock_client

    async def test_long_system_prompt_marked_cacheable(self):
        """A system prompt above the min size is sent as a cache_control block."""
        mock_client = self._mock_client()
        long_system = "You are a helpful assistant. " * 200  # > 4096 chars

        with (
            patch(
                "core.services.llm.providers.anthropic_provider.anthropic"
            ) as mock_anthropic,
            patch(
                "core.services.llm.providers.anthropic_provider._PROMPT_CACHE_ENABLED",
                True,
            ),
        ):
            mock_anthropic.AsyncAnthropic.return_value = mock_client
            from core.services.llm.providers.anthropic_provider import AnthropicProvider

            provider = AnthropicProvider(api_key="sk-ant-test")
            await provider.generate("hi", model="claude-3-sonnet", system=long_system)

        system_arg = mock_client.messages.create.call_args.kwargs["system"]
        assert isinstance(system_arg, list)
        assert system_arg[0]["cache_control"] == {"type": "ephemeral"}
        assert system_arg[0]["text"] == long_system

    async def test_short_system_prompt_stays_plain_string(self):
        """A short system prompt is not worth a cache breakpoint and stays a str."""
        mock_client = self._mock_client()

        with (
            patch(
                "core.services.llm.providers.anthropic_provider.anthropic"
            ) as mock_anthropic,
            patch(
                "core.services.llm.providers.anthropic_provider._PROMPT_CACHE_ENABLED",
                True,
            ),
        ):
            mock_anthropic.AsyncAnthropic.return_value = mock_client
            from core.services.llm.providers.anthropic_provider import AnthropicProvider

            provider = AnthropicProvider(api_key="sk-ant-test")
            await provider.generate("hi", model="claude-3-sonnet", system="short")

        system_arg = mock_client.messages.create.call_args.kwargs["system"]
        assert system_arg == "short"

    async def test_cache_disabled_sends_plain_string(self):
        """With caching disabled, even a long system prompt stays a plain str."""
        mock_client = self._mock_client()
        long_system = "You are a helpful assistant. " * 200

        with (
            patch(
                "core.services.llm.providers.anthropic_provider.anthropic"
            ) as mock_anthropic,
            patch(
                "core.services.llm.providers.anthropic_provider._PROMPT_CACHE_ENABLED",
                False,
            ),
        ):
            mock_anthropic.AsyncAnthropic.return_value = mock_client
            from core.services.llm.providers.anthropic_provider import AnthropicProvider

            provider = AnthropicProvider(api_key="sk-ant-test")
            await provider.generate("hi", model="claude-3-sonnet", system=long_system)

        system_arg = mock_client.messages.create.call_args.kwargs["system"]
        assert system_arg == long_system
