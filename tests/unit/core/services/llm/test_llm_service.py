"""
Unit tests for LLM service.

Tests the LLM service with mocked providers.
"""

import pytest
from unittest.mock import Mock, patch

from core.services.llm import LLMService
from core.services.llm.cost_control import CostTracker, estimate_tokens
from core.services.llm.exceptions import BudgetExceededError


class TestCostTracker:
    """Tests for CostTracker."""

    def test_track_tokens_within_budget(self):
        """Test tracking tokens within budget."""
        tracker = CostTracker(max_tokens=100)

        tracker.track_tokens(50)
        assert tracker.tokens_used == 50

        tracker.track_tokens(30)
        assert tracker.tokens_used == 80

    def test_track_tokens_exceeds_budget(self):
        """Test that exceeding budget raises error."""
        tracker = CostTracker(max_tokens=100)

        tracker.track_tokens(50)

        with pytest.raises(BudgetExceededError):
            tracker.track_tokens(60)  # Would exceed 100

    def test_track_tokens_no_limit(self):
        """Test tracking without limit."""
        tracker = CostTracker(max_tokens=None)

        tracker.track_tokens(1000)
        tracker.track_tokens(5000)

        assert tracker.tokens_used == 6000  # No error

    def test_get_usage(self):
        """Test getting usage statistics."""
        tracker = CostTracker(max_tokens=100)
        tracker.track_tokens(30)

        usage = tracker.get_usage()

        assert usage["tokens_used"] == 30
        assert usage["max_tokens"] == 100
        assert usage["remaining"] == 70


class TestEstimateTokens:
    """Tests for token estimation."""

    def test_estimate_tokens(self):
        """Test token estimation."""
        assert estimate_tokens("") == 0
        assert estimate_tokens("test") == 1  # 4 chars = 1 token
        assert estimate_tokens("test test") == 2  # 9 chars = 2 tokens
        # 100 chars = 13 tokens with tiktoken, ~25-33 with heuristic
        tokens = estimate_tokens("a" * 100)
        assert tokens in (13, 25)


class TestLLMService:
    """Tests for LLMService."""

    @patch("core.services.llm.service.TTLCache")
    @patch("core.services.llm.service.get_llm_config")
    @patch("core.services.llm.service.OllamaProvider")
    def test_initialization_ollama(self, mock_ollama, mock_config, mock_ttl_cache):
        """Test service initialization with Ollama."""
        mock_config.return_value = Mock(
            provider="ollama",
            model="llama3.2",
            api_base=None,
            enable_cache=True,
            cache_max_size=1000,
            cache_ttl=3600,
        )

        service = LLMService()

        assert service.config.provider == "ollama"
        assert service.enable_cache is True
        mock_ollama.assert_called_once()
        mock_ttl_cache.assert_called_once()

    @patch("core.services.llm.service.TTLCache")
    @patch("core.services.llm.service.get_llm_config")
    @patch("core.services.llm.service.OpenAIProvider")
    def test_initialization_openai(self, mock_openai, mock_config, mock_ttl_cache):
        """Test service initialization with OpenAI."""
        mock_config.return_value = Mock(
            provider="openai",
            model="gpt-4",
            api_key="test-key",
            enable_cache=True,
            cache_max_size=1000,
            cache_ttl=3600,
        )

        service = LLMService()

        assert service.config.provider == "openai"
        mock_openai.assert_called_once_with(api_key="test-key")
        mock_ttl_cache.assert_called_once()

    @patch("core.services.llm.service.get_llm_config")
    def test_initialization_openai_no_key(self, mock_config):
        """Test error when OpenAI key is missing."""
        from core.services.llm.exceptions import LLMProviderError

        mock_config.return_value = Mock(
            provider="openai",
            api_key=None,
            enable_cache=False,
            cache_max_size=10,
            cache_ttl=10,
        )
        with pytest.raises(LLMProviderError, match="OpenAI API key is required"):
            LLMService()

    @patch("core.services.llm.service.get_llm_config")
    @patch("core.services.llm.service.AnthropicProvider")
    def test_initialization_anthropic(self, mock_anthropic, mock_config):
        """Test service initialization with Anthropic."""
        mock_config.return_value = Mock(
            provider="anthropic",
            api_key="ant-key",
            enable_cache=False,
            cache_max_size=10,
            cache_ttl=10,
        )
        service = LLMService()
        assert service.config.provider == "anthropic"
        mock_anthropic.assert_called_once_with(api_key="ant-key")

    @patch("core.services.llm.service.get_llm_config")
    def test_initialization_unsupported(self, mock_config):
        """Test error for unsupported provider."""
        from core.services.llm.exceptions import LLMProviderError

        mock_config.return_value = Mock(
            provider="unsupported", enable_cache=False, cache_max_size=10, cache_ttl=10
        )
        with pytest.raises(LLMProviderError, match="Unsupported provider"):
            LLMService()

    @patch("core.services.llm.service.get_llm_config")
    @patch("core.services.llm.service.SemanticLLMCache")
    def test_initialization_semantic_cache(self, mock_semantic, mock_config):
        """Test semantic cache initialization."""
        mock_config.return_value = Mock(
            provider="ollama",
            model="m",
            enable_cache=False,
            cache_max_size=10,
            cache_ttl=10,
            api_base="http://localhost:11434",
        )
        service = LLMService(enable_semantic_cache=True)
        assert service.semantic_cache is not None
        mock_semantic.assert_called_once()

    @pytest.mark.asyncio
    @patch("core.services.llm.service.get_llm_config")
    async def test_generate_response(self, mock_config):
        """Test generating a response (async)."""
        mock_config.return_value = Mock(
            provider="ollama",
            model="llama3.2",
            api_base=None,
            enable_cache=False,
            cache_max_size=1000,
            cache_ttl=3600,
        )

        service = LLMService()

        # Mock provider and provider chain
        from unittest.mock import AsyncMock

        mock_provider = Mock()
        mock_provider.generate = AsyncMock(return_value=("Test response", 50))
        service.provider = mock_provider
        service._provider_chain = [mock_provider]

        response = await service.generate_response("Test prompt")

        assert response == "Test response"
        service.provider.generate.assert_called_once()

    @pytest.mark.asyncio
    @patch("core.services.llm.service.TTLCache")
    @patch("core.services.llm.service.get_llm_config")
    async def test_generate_response_with_cache(self, mock_config, mock_ttl_cache):
        """Test response caching (async)."""
        mock_config.return_value = Mock(
            provider="ollama",
            model="llama3.2",
            api_base=None,
            enable_cache=True,
            cache_max_size=1000,
            cache_ttl=3600,
        )

        # Mock Semantic Cache behavior
        from unittest.mock import AsyncMock

        mock_cache_instance = Mock()
        cache_storage = {}

        async def get_response_side_effect(prompt, **kwargs):
            return cache_storage.get(prompt)

        async def cache_response_side_effect(prompt, response, **kwargs):
            cache_storage[prompt] = response

        mock_cache_instance.get = AsyncMock(side_effect=get_response_side_effect)
        mock_cache_instance.set = AsyncMock(side_effect=cache_response_side_effect)

        mock_ttl_cache.return_value = mock_cache_instance

        service = LLMService()
        from unittest.mock import AsyncMock

        mock_provider = Mock()
        mock_provider.generate = AsyncMock(return_value=("Cached response", 50))
        service.provider = mock_provider
        service._provider_chain = [mock_provider]

        # First call - should hit provider
        response1 = await service.generate_response("Test prompt")
        assert response1 == "Cached response"
        assert service.provider.generate.call_count == 1

        # Verify cache was set
        # Cache key format: "{tenant}:{model}:{json}:{prompt}"
        expected_key = "default:llama3.2:False:Test prompt"
        assert expected_key in cache_storage

        # Second call - should hit cache
        response2 = await service.generate_response("Test prompt")
        assert response2 == "Cached response"
        assert service.provider.generate.call_count == 1  # Not called again

    @pytest.mark.asyncio
    @patch("core.services.llm.service.get_llm_config")
    async def test_generate_response_with_budget(self, mock_config):
        """Test generation with token budget (async)."""
        mock_config.return_value = Mock(
            provider="ollama",
            model="llama3.2",
            api_base=None,
            enable_cache=False,
            cache_max_size=1000,
            cache_ttl=3600,
        )

        tracker = CostTracker(max_tokens=100)
        service = LLMService(cost_tracker=tracker)
        from unittest.mock import AsyncMock

        mock_provider = Mock()
        mock_provider.generate = AsyncMock(return_value=("Response", 150))
        service.provider = mock_provider
        service._provider_chain = [mock_provider]

        # Should raise budget exceeded
        with pytest.raises(BudgetExceededError):
            await service.generate_response("Test prompt")

    @pytest.mark.asyncio
    @patch("core.services.llm.service.get_llm_config")
    async def test_generate_response_stream(self, mock_config):
        """Test streaming generation (async)."""
        mock_config.return_value = Mock(
            provider="ollama",
            model="llama3.2",
            api_base=None,
            enable_cache=False,
            cache_max_size=1000,
            cache_ttl=3600,
        )

        service = LLMService()
        mock_provider = Mock()
        service.provider = mock_provider

        # Mock async generator
        async def mock_stream_gen(*args, **kwargs):
            yield "Hello", 10
            yield " world", 20
            yield "!", 25

        mock_provider.generate_stream = mock_stream_gen
        service._provider_chain = [mock_provider]

        chunks = []
        async for chunk in service.generate_response_stream("Test prompt"):
            chunks.append(chunk)

        assert chunks == ["Hello", " world", "!"]

    @pytest.mark.asyncio
    @patch("core.services.llm.service.get_llm_config")
    async def test_generate_rate_limit_retry(self, mock_config):
        """Test retry logic on rate limit errors."""
        mock_config.return_value = Mock(
            provider="ollama", model="m", enable_cache=False
        )
        service = LLMService()

        from unittest.mock import AsyncMock

        mock_provider = Mock()
        # Fail once with 429, then succeed
        mock_provider.generate = AsyncMock(
            side_effect=[Exception("Rate limit 429 hit"), ("Success", 10)]
        )
        service.provider = mock_provider

        res = await service.generate_response("q")
        assert res == "Success"
        assert mock_provider.generate.call_count == 2

    @pytest.mark.asyncio
    @patch("core.services.llm.service.get_llm_config")
    async def test_generate_general_error(self, mock_config):
        """Test general error wrapping in generate_response."""
        from core.services.llm.exceptions import LLMProviderError

        mock_config.return_value = Mock(
            provider="ollama", model="m", enable_cache=False
        )
        service = LLMService()

        from unittest.mock import AsyncMock

        mock_provider = Mock()
        mock_provider.generate = AsyncMock(side_effect=Exception("Hard failure"))
        service.provider = mock_provider

        with pytest.raises(LLMProviderError, match="Generation failed"):
            await service.generate_response("q")

    @pytest.mark.asyncio
    @patch("core.services.llm.service.get_llm_config")
    async def test_generate_response_stream_error(self, mock_config):
        """Test error handling in streaming generation."""
        from core.services.llm.exceptions import LLMProviderError

        mock_config.return_value = Mock(
            provider="ollama", model="m", enable_cache=False
        )
        service = LLMService()

        mock_provider = Mock()

        async def failing_gen(*args, **kwargs):
            yield "Beginning", 1
            raise Exception("Stream broke")

        mock_provider.generate_stream = failing_gen
        service.provider = mock_provider

        with pytest.raises(LLMProviderError, match="Streaming failed"):
            async for _ in service.generate_response_stream("q"):
                pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
