"""
Tests for core/services/llm/providers/ollama_provider.py

Tests Ollama LLM provider with mocked API calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestOllamaProviderInit:
    """Tests for OllamaProvider initialization."""

    @patch("core.services.llm.providers.ollama_provider.ollama")
    def test_init_with_api_base(self, mock_ollama):
        """Verify provider initializes with custom API base."""
        mock_client = MagicMock()
        mock_ollama.AsyncClient.return_value = mock_client

        from core.services.llm.providers.ollama_provider import OllamaProvider

        provider = OllamaProvider(api_base="http://localhost:11434")

        assert provider.api_base == "http://localhost:11434"
        # Force initialization
        provider._ensure_client()
        mock_ollama.AsyncClient.assert_called_once_with(
            host="http://localhost:11434", timeout=provider._timeout
        )
        # The explicit deadline guards against a hung local server.
        assert provider._timeout.connect is not None
        assert provider._timeout.read is not None

    @patch("core.services.llm.providers.ollama_provider.ollama")
    def test_init_without_api_base(self, mock_ollama):
        """Verify provider initializes with default settings."""
        mock_client = MagicMock()
        mock_ollama.AsyncClient.return_value = mock_client
        from core.services.llm.providers.ollama_provider import OllamaProvider

        # Clear env var
        with patch.dict("os.environ", {}, clear=True):
            provider = OllamaProvider(api_base=None)
            # Force initialization
            provider._ensure_client()

        assert provider.client is not None
        mock_ollama.AsyncClient.assert_called_once()

    @patch("core.services.llm.providers.ollama_provider.ollama")
    def test_init_uses_the_dedicated_ollama_endpoint(self, mock_ollama):
        """The dedicated ``LLM_OLLAMA_API_BASE`` wins for this provider."""
        from core.config.services import LLMConfig
        from core.services.llm.providers.ollama_provider import OllamaProvider

        # Set through the environment, the way an operator does. The dict is
        # cleared first because ``LLMConfig`` is a ``BaseSettings``: the repo's
        # own ``.env`` would otherwise leak into the fields under test.
        with patch.dict(
            "os.environ",
            {"LLM_OLLAMA_API_BASE": "http://ollama-box:11434"},
            clear=True,
        ):
            config = LLMConfig(
                provider="openai",
                model="gpt-4o-mini",
                api_base="https://openai-gateway.internal/v1",
            )
        with patch("core.config.services.get_llm_config", return_value=config):
            provider = OllamaProvider()

        assert provider.api_base == "http://ollama-box:11434"

    @patch("core.services.llm.providers.ollama_provider.ollama")
    def test_init_uses_api_base_when_ollama_is_the_default(self, mock_ollama):
        """With Ollama as the deployment default, ``api_base`` is its endpoint."""
        from core.config.services import LLMConfig
        from core.services.llm.providers.ollama_provider import OllamaProvider

        with patch.dict("os.environ", {}, clear=True):
            config = LLMConfig(
                provider="ollama", model="llama3.2", api_base="http://env-host:11434"
            )
        with patch("core.config.services.get_llm_config", return_value=config):
            provider = OllamaProvider()

        assert provider.api_base == "http://env-host:11434"

    @patch("core.services.llm.providers.ollama_provider.ollama")
    def test_init_ignores_another_providers_api_base(self, mock_ollama):
        """A policy-routed Ollama call must not inherit the default's endpoint.

        ``api_base`` belongs to the deployment's *default* provider. Falling
        back to it here aimed every policy-pinned Ollama call at, say, an
        OpenAI-compatible gateway — which serves nothing on ``/api/chat``, so
        the call stalled until the read timeout and surfaced as a timeout: a
        misconfiguration wearing the costume of a slow model.
        """
        from core.config.services import LLMConfig
        from core.services.llm.providers.ollama_provider import OllamaProvider

        with patch.dict("os.environ", {}, clear=True):
            config = LLMConfig(
                provider="openai",
                model="gpt-4o-mini",
                api_base="https://openai-gateway.internal/v1",
            )
        with (
            patch("core.config.services.get_llm_config", return_value=config),
            patch.dict("os.environ", {}, clear=True),
        ):
            provider = OllamaProvider()

        assert provider.api_base is None


@pytest.mark.asyncio
class TestOllamaProviderGenerate:
    """Tests for OllamaProvider.generate method."""

    async def test_generate_returns_content_and_tokens(self):
        """Verify generate returns tuple of content and tokens."""
        mock_client = AsyncMock()
        mock_response = {
            "message": {"content": "Hello, world!"},
            "eval_count": 10,
            "prompt_eval_count": 5,
        }
        mock_client.chat.return_value = mock_response

        with patch("core.services.llm.providers.ollama_provider.ollama") as mock_ollama:
            mock_ollama.AsyncClient.return_value = mock_client
            from core.services.llm.providers.ollama_provider import OllamaProvider

            provider = OllamaProvider()
            content, tokens = await provider.generate("Test prompt", model="llama3.2")

            assert content == "Hello, world!"
            assert tokens == 15

    async def test_generate_with_client(self):
        """Verify generate uses client when available."""
        mock_client = AsyncMock()
        mock_response = {"message": {"content": "Response"}, "eval_count": 5}
        mock_client.chat.return_value = mock_response

        with patch("core.services.llm.providers.ollama_provider.ollama") as mock_ollama:
            mock_ollama.AsyncClient.return_value = mock_client
            from core.services.llm.providers.ollama_provider import OllamaProvider

            provider = OllamaProvider(api_base="http://localhost:11434")
            content, tokens = await provider.generate("Prompt", model="llama3.2")

            mock_client.chat.assert_called_once()
            assert content == "Response"

    async def test_generate_with_json_mode(self):
        """Verify generate passes json_mode as format parameter."""
        mock_client = AsyncMock()
        mock_response = {"message": {"content": '{"key": "value"}'}}
        mock_client.chat.return_value = mock_response

        with patch("core.services.llm.providers.ollama_provider.ollama") as mock_ollama:
            mock_ollama.AsyncClient.return_value = mock_client
            from core.services.llm.providers.ollama_provider import OllamaProvider

            provider = OllamaProvider()
            await provider.generate("Prompt", model="llama3.2", json_mode=True)

            call_kwargs = mock_client.chat.call_args[1]
            assert call_kwargs.get("format") == "json"

    async def test_generate_raises_on_error(self):
        """Verify generate raises LLMProviderError on exception."""
        mock_client = AsyncMock()
        mock_client.chat.side_effect = Exception("API error")

        with patch("core.services.llm.providers.ollama_provider.ollama") as mock_ollama:
            mock_ollama.AsyncClient.return_value = mock_client
            from core.services.llm.exceptions import LLMProviderError
            from core.services.llm.providers.ollama_provider import OllamaProvider

            provider = OllamaProvider()

            with pytest.raises(LLMProviderError) as exc_info:
                await provider.generate("Prompt", model="llama3.2")

            assert "Ollama error" in str(exc_info.value)


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
class TestOllamaProviderGenerateStream:
    """Tests for OllamaProvider.generate_stream method."""

    async def test_generate_stream_yields_chunks(self):
        """Verify generate_stream yields content chunks."""
        mock_client = AsyncMock()
        mock_chunks = [
            {"message": {"content": "Hello"}},
            {"message": {"content": " world"}},
            {"message": {"content": "!"}},
        ]
        mock_client.chat.return_value = AsyncIterator(mock_chunks)

        with patch("core.services.llm.providers.ollama_provider.ollama") as mock_ollama:
            mock_ollama.AsyncClient.return_value = mock_client
            from core.services.llm.providers.ollama_provider import OllamaProvider

            provider = OllamaProvider()
            chunks = []
            async for chunk in provider.generate_stream("Prompt", model="llama3.2"):
                chunks.append(chunk)

            assert len(chunks) == 3
            assert chunks[0][0] == "Hello"
            assert chunks[1][0] == "world"
            assert chunks[2][0] == "!"

    async def test_generate_stream_accumulates_tokens(self):
        """Verify generate_stream accumulates token counts."""
        mock_client = AsyncMock()
        mock_chunks = [
            {"message": {"content": "A"}},
            {"message": {"content": "B"}},
        ]
        mock_client.chat.return_value = AsyncIterator(mock_chunks)

        with patch("core.services.llm.providers.ollama_provider.ollama") as mock_ollama:
            mock_ollama.AsyncClient.return_value = mock_client
            from core.services.llm.providers.ollama_provider import OllamaProvider

            provider = OllamaProvider()
            chunks = []
            async for chunk in provider.generate_stream("Prompt", model="llama3.2"):
                chunks.append(chunk)

            # Second chunk should have more accumulated tokens
            assert chunks[1][1] >= chunks[0][1]

    async def test_generate_stream_raises_on_error(self):
        """Verify generate_stream raises LLMProviderError on exception."""
        mock_client = AsyncMock()
        mock_client.chat.side_effect = Exception("Stream error")

        with patch("core.services.llm.providers.ollama_provider.ollama") as mock_ollama:
            mock_ollama.AsyncClient.return_value = mock_client
            from core.services.llm.exceptions import LLMProviderError
            from core.services.llm.providers.ollama_provider import OllamaProvider

            provider = OllamaProvider()

            with pytest.raises(LLMProviderError) as exc_info:
                async for _ in provider.generate_stream("Prompt", model="llama3.2"):
                    pass

            assert "streaming error" in str(exc_info.value)


class TestOllamaProviderHelpers:
    """Tests for OllamaProvider helper methods."""

    @patch("core.services.llm.providers.ollama_provider.ollama")
    def test_extract_content_from_dict(self, mock_ollama):
        """Verify _extract_content handles dict response."""
        from core.services.llm.providers.ollama_provider import OllamaProvider

        provider = OllamaProvider()

        response = {"message": {"content": "  Content with spaces  "}}
        result = provider._extract_content(response)

        assert result == "Content with spaces"

    @patch("core.services.llm.providers.ollama_provider.ollama")
    def test_extract_content_from_object(self, mock_ollama):
        """Verify _extract_content handles object response."""
        from core.services.llm.providers.ollama_provider import OllamaProvider

        provider = OllamaProvider()

        response = MagicMock()
        response.message.content = "  Object content  "
        result = provider._extract_content(response)

        assert result == "Object content"

    @patch("core.services.llm.providers.ollama_provider.ollama")
    def test_extract_tokens_from_response(self, mock_ollama):
        """Verify _extract_tokens extracts token counts."""
        from core.services.llm.providers.ollama_provider import OllamaProvider

        provider = OllamaProvider()

        response = {"eval_count": 100, "prompt_eval_count": 50}
        result = provider._extract_tokens(response, "prompt", "content")

        assert result == 150

    @patch("core.services.llm.providers.ollama_provider.ollama")
    def test_extract_tokens_estimates_when_missing(self, mock_ollama):
        """Verify _extract_tokens estimates when counts not available."""
        from core.services.llm.providers.ollama_provider import OllamaProvider

        provider = OllamaProvider()

        response = {}  # No token counts
        result = provider._extract_tokens(response, "prompt", "content")

        # Should return estimated value > 0
        assert result > 0
