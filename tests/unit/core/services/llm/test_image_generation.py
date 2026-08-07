"""Image generation through the provider layer."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.services.llm.exceptions import LLMProviderError
from core.services.llm.images import GeneratedImage, generate_image

PNG = b"\x89PNG\r\n\x1a\n-not-a-real-png"


class _Provider:
    async def generate_image(
        self, prompt, *, model=None, size=None, quality=None, **kwargs
    ):
        self.seen = (prompt, model, size, quality)
        return GeneratedImage(data=PNG, media_type="image/png", model=model or "d")


async def test_the_seam_returns_bytes_from_the_active_provider() -> None:
    service = SimpleNamespace(provider=_Provider())

    image = await generate_image(
        service, "a lighthouse", model="m", size="1024x1024", quality="low"
    )

    assert image.data == PNG
    assert image.media_type == "image/png"
    assert service.provider.seen == ("a lighthouse", "m", "1024x1024", "low")


async def test_a_provider_without_images_says_so() -> None:
    """The alternative is an AttributeError deep inside a pipeline stage."""
    service = SimpleNamespace(provider=SimpleNamespace())

    with pytest.raises(LLMProviderError) as exc:
        await generate_image(service, "anything")

    assert "cannot generate images" in str(exc.value)


class TestOpenAIImages:
    async def test_base64_payload_is_decoded(self) -> None:
        mock_client = AsyncMock()
        item = MagicMock()
        item.b64_json = base64.b64encode(PNG).decode()
        item.revised_prompt = "a lighthouse at dusk, wide"
        mock_client.images.generate.return_value = MagicMock(data=[item])

        with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
            mock_openai.AsyncOpenAI.return_value = mock_client
            from core.services.llm.providers.openai_provider import OpenAIProvider

            image = await OpenAIProvider(api_key="sk-test").generate_image(
                "a lighthouse", size="1536x1024", quality="low"
            )

        assert image.data == PNG
        assert image.revised_prompt == "a lighthouse at dusk, wide"
        sent = mock_client.images.generate.call_args.kwargs
        assert sent["size"] == "1536x1024"
        assert sent["n"] == 1
        assert sent["quality"] == "low"

    async def test_no_quality_means_no_quality_key_in_the_request(self) -> None:
        """The accepted tiers differ per model; an unknown one is a 400."""
        mock_client = AsyncMock()
        item = MagicMock()
        item.b64_json = base64.b64encode(PNG).decode()
        mock_client.images.generate.return_value = MagicMock(data=[item])

        with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
            mock_openai.AsyncOpenAI.return_value = mock_client
            from core.services.llm.providers.openai_provider import OpenAIProvider

            await OpenAIProvider(api_key="sk-test").generate_image("a lighthouse")

        assert "quality" not in mock_client.images.generate.call_args.kwargs

    async def test_a_url_only_response_is_an_error_not_an_empty_image(self) -> None:
        """Returning empty bytes would commit a broken file to a repository."""
        mock_client = AsyncMock()
        item = MagicMock()
        item.b64_json = None
        mock_client.images.generate.return_value = MagicMock(data=[item])

        with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
            mock_openai.AsyncOpenAI.return_value = mock_client
            from core.services.llm.providers.openai_provider import OpenAIProvider

            with pytest.raises(LLMProviderError) as exc:
                await OpenAIProvider(api_key="sk-test").generate_image("x")

        assert "no image data" in str(exc.value)
