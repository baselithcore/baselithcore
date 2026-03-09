"""
Tests for core/services/llm/providers/huggingface_provider.py

Tests HuggingFace LLM provider with mocked API calls.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestHuggingFaceProviderInit:
    """Tests for HuggingFaceProvider initialization."""

    @patch(
        "core.services.llm.providers.huggingface_provider.HuggingFaceProvider._init_inference_client"
    )
    def test_init_with_api_key(self, mock_init_client):
        """Verify provider initializes with API key for Inference API mode."""
        from core.services.llm.providers.huggingface_provider import HuggingFaceProvider

        provider = HuggingFaceProvider(api_key="hf_test_token")

        assert provider.api_key == "hf_test_token"
        assert provider.use_local is False
        # Force initialization
        provider._init_inference_client()
        mock_init_client.assert_called_once()

    def test_init_without_api_key_for_inference_raises(self):
        """Verify provider raises error without API key in Inference API mode."""
        from core.services.llm.providers.huggingface_provider import HuggingFaceProvider
        from core.services.llm.exceptions import LLMProviderError

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(LLMProviderError) as exc_info:
                HuggingFaceProvider(api_key=None, use_local=False)

        assert "API key is required" in str(exc_info.value)

    def test_init_local_mode_without_api_key(self):
        """Verify local mode doesn't require API key."""
        from core.services.llm.providers.huggingface_provider import HuggingFaceProvider

        # Local mode should not raise even without API key
        provider = HuggingFaceProvider(use_local=True)

        assert provider.use_local is True
        assert provider._inference_client is None

    def test_init_with_custom_device(self):
        """Verify custom device setting is stored."""
        from core.services.llm.providers.huggingface_provider import HuggingFaceProvider

        provider = HuggingFaceProvider(use_local=True, device="cuda")

        assert provider.device == "cuda"

    def test_init_with_custom_dtype(self):
        """Verify custom dtype setting is stored."""
        from core.services.llm.providers.huggingface_provider import HuggingFaceProvider

        provider = HuggingFaceProvider(use_local=True, torch_dtype="float16")

        assert provider.torch_dtype == "float16"


class TestHuggingFaceProviderGenerateInferenceAPI:
    """Tests for HuggingFaceProvider.generate with Inference API."""

    @patch(
        "core.services.llm.providers.huggingface_provider.HuggingFaceProvider._init_inference_client"
    )
    @pytest.mark.asyncio
    async def test_generate_returns_content_and_tokens(self, mock_init):
        """Verify generate returns tuple of content and tokens."""
        from core.services.llm.providers.huggingface_provider import HuggingFaceProvider

        provider = HuggingFaceProvider(api_key="hf_test")

        # Mock the inference client
        mock_client = MagicMock()
        mock_client.text_generation.return_value = "  Hello, world!  "
        provider._inference_client = mock_client

        content, tokens = await provider.generate(
            "Test prompt", model="mistralai/Mistral-7B"
        )

        assert content == "Hello, world!"
        assert tokens > 0
        mock_client.text_generation.assert_called_once()

    @patch(
        "core.services.llm.providers.huggingface_provider.HuggingFaceProvider._init_inference_client"
    )
    @pytest.mark.asyncio
    async def test_generate_with_json_mode(self, mock_init):
        """Verify generate appends JSON instruction in json_mode."""
        from core.services.llm.providers.huggingface_provider import HuggingFaceProvider

        provider = HuggingFaceProvider(api_key="hf_test")

        mock_client = MagicMock()
        mock_client.text_generation.return_value = '{"key": "value"}'
        provider._inference_client = mock_client

        await provider.generate("Prompt", model="model", json_mode=True)

        call_args = mock_client.text_generation.call_args
        prompt_sent = call_args[0][0]
        assert "JSON" in prompt_sent

    @patch(
        "core.services.llm.providers.huggingface_provider.HuggingFaceProvider._init_inference_client"
    )
    @pytest.mark.asyncio
    async def test_generate_raises_on_error(self, mock_init):
        """Verify generate raises LLMProviderError on exception."""
        from core.services.llm.providers.huggingface_provider import HuggingFaceProvider
        from core.services.llm.exceptions import LLMProviderError

        provider = HuggingFaceProvider(api_key="hf_test")

        mock_client = MagicMock()
        mock_client.text_generation.side_effect = Exception("API error")
        provider._inference_client = mock_client

        with pytest.raises(LLMProviderError) as exc_info:
            await provider.generate("Prompt", model="model")

        assert "HuggingFace error" in str(exc_info.value)


class TestHuggingFaceProviderGenerateLocal:
    """Tests for HuggingFaceProvider.generate with local transformers."""

    @pytest.mark.asyncio
    async def test_generate_local_returns_content_and_tokens(self):
        """Verify local generate returns tuple of content and tokens."""
        from core.services.llm.providers.huggingface_provider import HuggingFaceProvider

        provider = HuggingFaceProvider(use_local=True)

        # Mock the local pipeline
        mock_pipeline = MagicMock()
        mock_pipeline.return_value = [{"generated_text": "  Hello from local!  "}]
        provider._local_pipeline = mock_pipeline
        provider._current_model = "test-model"

        content, tokens = await provider.generate("Test prompt", model="test-model")

        assert content == "Hello from local!"
        assert tokens > 0

    @pytest.mark.asyncio
    async def test_generate_local_with_json_mode(self):
        """Verify local generate appends JSON instruction in json_mode."""
        from core.services.llm.providers.huggingface_provider import HuggingFaceProvider

        provider = HuggingFaceProvider(use_local=True)

        mock_pipeline = MagicMock()
        mock_pipeline.return_value = [{"generated_text": '{"key": "value"}'}]
        provider._local_pipeline = mock_pipeline
        provider._current_model = "test-model"

        await provider.generate("Prompt", model="test-model", json_mode=True)

        call_args = mock_pipeline.call_args
        prompt_sent = call_args[0][0]
        assert "JSON" in prompt_sent


class TestHuggingFaceProviderGenerateStream:
    """Tests for HuggingFaceProvider.generate_stream method."""

    @patch(
        "core.services.llm.providers.huggingface_provider.HuggingFaceProvider._init_inference_client"
    )
    @pytest.mark.asyncio
    async def test_generate_stream_yields_chunks(self, mock_init):
        """Verify generate_stream yields content chunks."""
        from core.services.llm.providers.huggingface_provider import HuggingFaceProvider

        provider = HuggingFaceProvider(api_key="hf_test")

        mock_client = MagicMock()
        mock_client.text_generation.return_value = iter(["Hello", " world", "!"])
        provider._inference_client = mock_client

        chunks = [
            chunk async for chunk in provider.generate_stream("Prompt", model="model")
        ]

        assert len(chunks) == 3
        assert chunks[0][0] == "Hello"
        assert chunks[1][0] == " world"
        assert chunks[2][0] == "!"

    @patch(
        "core.services.llm.providers.huggingface_provider.HuggingFaceProvider._init_inference_client"
    )
    @pytest.mark.asyncio
    async def test_generate_stream_accumulates_tokens(self, mock_init):
        """Verify generate_stream accumulates token counts."""
        from core.services.llm.providers.huggingface_provider import HuggingFaceProvider

        provider = HuggingFaceProvider(api_key="hf_test")

        mock_client = MagicMock()
        mock_client.text_generation.return_value = iter(["A", "B"])
        provider._inference_client = mock_client

        chunks = [
            chunk async for chunk in provider.generate_stream("Prompt", model="model")
        ]

        # Second chunk should have more accumulated tokens than first
        assert chunks[1][1] >= chunks[0][1]

    @patch(
        "core.services.llm.providers.huggingface_provider.HuggingFaceProvider._init_inference_client"
    )
    @pytest.mark.asyncio
    async def test_generate_stream_raises_on_error(self, mock_init):
        """Verify generate_stream raises LLMProviderError on exception."""
        from core.services.llm.providers.huggingface_provider import HuggingFaceProvider
        from core.services.llm.exceptions import LLMProviderError

        provider = HuggingFaceProvider(api_key="hf_test")

        mock_client = MagicMock()
        mock_client.text_generation.side_effect = Exception("Stream error")
        provider._inference_client = mock_client

        with pytest.raises(LLMProviderError) as exc_info:
            _ = [
                chunk
                async for chunk in provider.generate_stream("Prompt", model="model")
            ]

        assert "streaming error" in str(exc_info.value)


class TestHuggingFaceProviderImportError:
    """Tests for handling missing dependencies."""

    def test_init_without_huggingface_hub_raises(self):
        """Verify appropriate error when huggingface-hub is not installed."""
        from core.services.llm.providers.huggingface_provider import HuggingFaceProvider
        from core.services.llm.exceptions import LLMProviderError

        with patch.dict("sys.modules", {"huggingface_hub": None}):
            # We must NOT patch _init_inference_client here because we want to test the REAL failure
            # when it's eventually called.

            provider = HuggingFaceProvider(api_key="hf_test")
            with pytest.raises((LLMProviderError, ImportError)):
                provider._generate_inference_api("prompt", "model")
