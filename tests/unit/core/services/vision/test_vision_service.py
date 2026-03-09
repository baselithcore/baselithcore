"""Tests for Vision Service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.services.vision.service import VisionService
from core.services.vision.models import (
    VisionProvider,
    ImageContent,
    VisionRequest,
    VisionCapability,
)


@pytest.fixture
def vision_service():
    # Patch config for Google API key
    with patch("core.services.vision.service.get_vision_config") as mock_config:
        mock_config.return_value.openai_api_key = "fake-key"
        mock_config.return_value.anthropic_api_key = "fake-key"
        mock_config.return_value.google_api_key = "fake-google-key"
        mock_config.return_value.ollama_url = "http://localhost:11434"

        service = VisionService(openai_api_key="fake-key", anthropic_api_key="fake-key")
        yield service


@pytest.mark.asyncio
async def test_init(vision_service):
    assert vision_service.default_provider == VisionProvider.OPENAI
    assert vision_service._openai_key == "fake-key"
    assert vision_service._anthropic_key == "fake-key"


@pytest.mark.asyncio
async def test_analyze_openai(vision_service):
    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_response = MagicMock()
        # Mock successful response
        mock_response.choices = [MagicMock(message=MagicMock(content="A cat"))]
        mock_response.usage.total_tokens = 100
        mock_client.chat.completions.create.return_value = mock_response

        request = VisionRequest(
            prompt="What is this?",
            images=[ImageContent.from_url("http://example.com/cat.jpg")],
            provider=VisionProvider.OPENAI,
        )
        response = await vision_service.analyze(request)

        assert response.success
        assert response.content == "A cat"
        assert response.provider == "openai"
        mock_client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_analyze_anthropic(vision_service):
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "content": [{"text": "A dog"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        request = VisionRequest(
            prompt="Describe",
            images=[ImageContent.from_base64("base64_data")],
            provider=VisionProvider.ANTHROPIC,
        )
        response = await vision_service.analyze(request)

        assert response.success
        assert response.content == "A dog"
        assert response.provider == "anthropic"
        mock_client.post.assert_awaited_once()
        assert "anthropic.com" in mock_client.post.call_args[0][0]


@pytest.mark.asyncio
async def test_analyze_google(vision_service):
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "A bird"}]}}],
            "usageMetadata": {"totalTokenCount": 20},
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        request = VisionRequest(
            prompt="Describe",
            images=[ImageContent.from_base64("fake_data", media_type="image/jpeg")],
            provider=VisionProvider.GOOGLE,
        )
        response = await vision_service.analyze(request)

        assert response.success
        assert response.content == "A bird"
        assert response.provider == "google"
        mock_client.post.assert_awaited_once()
        assert "googleapis.com" in mock_client.post.call_args[0][0]


@pytest.mark.asyncio
async def test_analyze_ollama(vision_service):
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "A llama", "eval_count": 50}
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        request = VisionRequest(
            prompt="Describe",
            images=[ImageContent.from_base64("fake_data")],
            provider=VisionProvider.OLLAMA,
        )
        response = await vision_service.analyze(request)

        assert response.success
        assert response.content == "A llama"
        assert response.provider == "ollama"
        mock_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_convenience_methods(vision_service):
    mock_resp = MagicMock()
    mock_resp.content = "Analysis result"
    mock_resp.tokens_used = 10

    with patch.object(
        vision_service, "analyze", new_callable=AsyncMock
    ) as mock_analyze:
        mock_analyze.return_value = mock_resp
        image = ImageContent.from_base64("test")

        # Test describe_image
        res = await vision_service.describe_image(image)
        assert res == "Analysis result"
        assert (
            mock_analyze.call_args[0][0].capability == VisionCapability.IMAGE_ANALYSIS
        )

        # Test extract_text
        res = await vision_service.extract_text(image)
        assert res == "Analysis result"
        assert mock_analyze.call_args[0][0].capability == VisionCapability.OCR

        # Test analyze_screenshot
        res = await vision_service.analyze_screenshot(image, "Where is the button?")
        assert res == "Analysis result"
        req = mock_analyze.call_args[0][0]
        assert req.capability == VisionCapability.SCREENSHOT_ANALYSIS
        assert req.prompt == "Where is the button?"

        # Test analyze_diagram
        res = await vision_service.analyze_diagram(image)
        assert res == "Analysis result"
        assert (
            mock_analyze.call_args[0][0].capability
            == VisionCapability.DIAGRAM_UNDERSTANDING
        )

        # Test compare_images
        res = await vision_service.compare_images(image, image)
        assert res == "Analysis result"
        req = mock_analyze.call_args[0][0]
        assert req.capability == VisionCapability.COMPARISON
        assert len(req.images) == 2


@pytest.mark.asyncio
async def test_build_prompt(vision_service):
    # Test that capability prompts are added
    req = VisionRequest(
        prompt="User prompt", images=[], capability=VisionCapability.OCR
    )
    prompt = vision_service._build_prompt(req)
    assert "Extract all text" in prompt
    assert "User prompt" in prompt

    # Test JSON mode
    req = VisionRequest(prompt="User prompt", images=[], json_mode=True)
    prompt = vision_service._build_prompt(req)
    assert "Respond only with valid JSON" in prompt


@pytest.mark.asyncio
async def test_missing_api_keys():
    with patch("core.services.vision.service.get_vision_config") as mock_config:
        mock_config.return_value.openai_api_key = None
        mock_config.return_value.anthropic_api_key = None
        mock_config.return_value.google_api_key = None

        empty_service = VisionService(
            openai_api_key=None, anthropic_api_key=None, google_api_key=None
        )

        req = VisionRequest(prompt="hi", images=[])

        # OpenAI
        req.provider = VisionProvider.OPENAI
        with pytest.raises(ValueError, match="OpenAI API key"):
            await empty_service.analyze(req)

        # Anthropic
        req.provider = VisionProvider.ANTHROPIC
        with pytest.raises(ValueError, match="Anthropic API key"):
            await empty_service.analyze(req)

        # Google
        req.provider = VisionProvider.GOOGLE
        with pytest.raises(ValueError, match="Google API key"):
            await empty_service.analyze(req)
