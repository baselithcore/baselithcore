"""
Tests for core/services/llm/providers/openai_provider.py

Tests OpenAI LLM provider with mocked API calls.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


class TestOpenAIProviderInit:
    """Tests for OpenAIProvider initialization."""

    @patch("core.services.llm.providers.openai_provider.openai")
    def test_init_with_api_key(self, mock_openai):
        """Verify provider initializes with API key."""
        mock_client = MagicMock()
        mock_openai.AsyncClient.return_value = mock_client

        from core.services.llm.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key="sk-test-key")
        # Force initialization
        provider._ensure_client()

        mock_openai.AsyncClient.assert_called_once_with(api_key="sk-test-key")
        assert provider.client is not None

    @patch("core.services.llm.providers.openai_provider.openai")
    def test_init_without_api_key_raises(self, mock_openai):
        """Verify provider raises error without API key."""
        from core.services.llm.providers.openai_provider import OpenAIProvider
        from core.services.llm.exceptions import LLMProviderError

        with pytest.raises(LLMProviderError) as exc_info:
            OpenAIProvider(api_key="")

        assert "API key is required" in str(exc_info.value)

    @patch("core.services.llm.providers.openai_provider.openai")
    def test_init_with_none_api_key_raises(self, mock_openai):
        """Verify provider raises error with None API key."""
        from core.services.llm.providers.openai_provider import OpenAIProvider
        from core.services.llm.exceptions import LLMProviderError

        with pytest.raises(LLMProviderError):
            OpenAIProvider(api_key=None)


@pytest.mark.asyncio
class TestOpenAIProviderGenerate:
    """Tests for OpenAIProvider.generate method."""

    async def test_generate_returns_content_and_tokens(self):
        """Verify generate returns tuple of content and tokens."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "  Hello, world!  "
        mock_response.usage.total_tokens = 25
        mock_client.chat.completions.create.return_value = mock_response

        with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
            mock_openai.AsyncClient.return_value = mock_client
            from core.services.llm.providers.openai_provider import OpenAIProvider

            provider = OpenAIProvider(api_key="sk-test")
            content, tokens = await provider.generate("Test prompt", model="gpt-4")

            assert content == "Hello, world!"
            assert tokens == 25

    async def test_generate_with_json_mode(self):
        """Verify generate passes json_mode as response_format."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"key": "value"}'
        mock_response.usage.total_tokens = 10
        mock_client.chat.completions.create.return_value = mock_response

        with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
            mock_openai.AsyncClient.return_value = mock_client
            from core.services.llm.providers.openai_provider import OpenAIProvider

            provider = OpenAIProvider(api_key="sk-test")
            await provider.generate("Prompt", model="gpt-4", json_mode=True)

            call_kwargs = mock_client.chat.completions.create.call_args[1]
            assert call_kwargs.get("response_format") == {"type": "json_object"}

    async def test_generate_estimates_tokens_when_usage_missing(self):
        """Verify generate estimates tokens when usage is None."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Response"
        mock_response.usage = None
        mock_client.chat.completions.create.return_value = mock_response

        with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
            mock_openai.AsyncClient.return_value = mock_client
            from core.services.llm.providers.openai_provider import OpenAIProvider

            provider = OpenAIProvider(api_key="sk-test")
            content, tokens = await provider.generate("Prompt", model="gpt-4")

            assert content == "Response"
            assert tokens > 0  # Should be estimated

    async def test_generate_raises_on_error(self):
        """Verify generate raises LLMProviderError on exception."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
            mock_openai.AsyncClient.return_value = mock_client
            from core.services.llm.providers.openai_provider import OpenAIProvider
            from core.services.llm.exceptions import LLMProviderError

            provider = OpenAIProvider(api_key="sk-test")

            with pytest.raises(LLMProviderError) as exc_info:
                await provider.generate("Prompt", model="gpt-4")

            assert "OpenAI error" in str(exc_info.value)


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
class TestOpenAIProviderGenerateStream:
    """Tests for OpenAIProvider.generate_stream method."""

    async def test_generate_stream_yields_chunks(self):
        """Verify generate_stream yields content chunks."""
        mock_client = AsyncMock()

        # Create mock chunks
        mock_chunks = []
        for content in ["Hello", " world", "!"]:
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = content
            mock_chunks.append(chunk)

        mock_client.chat.completions.create.return_value = AsyncIterator(mock_chunks)

        with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
            mock_openai.AsyncClient.return_value = mock_client
            from core.services.llm.providers.openai_provider import OpenAIProvider

            provider = OpenAIProvider(api_key="sk-test")
            chunks = []
            async for chunk in provider.generate_stream("Prompt", model="gpt-4"):
                chunks.append(chunk)

            assert len(chunks) == 3
            assert chunks[0][0] == "Hello"
            assert chunks[1][0] == " world"
            assert chunks[2][0] == "!"

    async def test_generate_stream_skips_empty_content(self):
        """Verify generate_stream skips chunks with no content."""
        mock_client = AsyncMock()

        mock_chunks = []
        # First chunk with content
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta.content = "Content"
        mock_chunks.append(chunk1)
        # Second chunk with empty content
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta.content = ""
        mock_chunks.append(chunk2)
        # Third chunk with None content
        chunk3 = MagicMock()
        chunk3.choices = [MagicMock()]
        chunk3.choices[0].delta.content = None
        mock_chunks.append(chunk3)

        mock_client.chat.completions.create.return_value = AsyncIterator(mock_chunks)

        with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
            mock_openai.AsyncClient.return_value = mock_client
            from core.services.llm.providers.openai_provider import OpenAIProvider

            provider = OpenAIProvider(api_key="sk-test")
            chunks = []
            async for chunk in provider.generate_stream("Prompt", model="gpt-4"):
                chunks.append(chunk)

            # Should only yield the first chunk with actual content
            assert len(chunks) == 1
            assert chunks[0][0] == "Content"

    async def test_generate_stream_raises_on_error(self):
        """Verify generate_stream raises LLMProviderError on exception."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = Exception("Stream error")

        with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
            mock_openai.AsyncClient.return_value = mock_client
            from core.services.llm.providers.openai_provider import OpenAIProvider
            from core.services.llm.exceptions import LLMProviderError

            provider = OpenAIProvider(api_key="sk-test")

            with pytest.raises(LLMProviderError) as exc_info:
                async for _ in provider.generate_stream("Prompt", model="gpt-4"):
                    pass

            assert "streaming error" in str(exc_info.value)

    async def test_generate_stream_accumulates_tokens(self):
        """Verify generate_stream accumulates token counts."""
        mock_client = AsyncMock()

        mock_chunks = []
        for content in ["A", "B"]:
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = content
            mock_chunks.append(chunk)

        mock_client.chat.completions.create.return_value = AsyncIterator(mock_chunks)

        with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
            mock_openai.AsyncClient.return_value = mock_client
            from core.services.llm.providers.openai_provider import OpenAIProvider

            provider = OpenAIProvider(api_key="sk-test")
            chunks = []
            async for chunk in provider.generate_stream("Prompt", model="gpt-4"):
                chunks.append(chunk)

            # Second chunk should have more accumulated tokens than first
            assert chunks[1][1] >= chunks[0][1]
