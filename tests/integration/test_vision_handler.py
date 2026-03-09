import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from core.orchestration.handlers.vision import VisionHandler
from core.services.vision import VisionResponse, ImageContent


@pytest.fixture
def mock_vision_service():
    mock = MagicMock()

    async def mock_analyze(request):
        return VisionResponse(
            success=True,
            content="This is a mocked description of a cat.",
            provider="openai",
            model="gpt-4o",
            tokens_used=100,
            raw_response={},
        )

    mock.analyze = AsyncMock(side_effect=mock_analyze)
    return mock


@pytest.mark.asyncio
async def test_vision_handler_flow(mock_vision_service):
    """
    Verify Vision Handler flow:
    1. Orchestrator passes context with image paths
    2. Handler creates ImageContent
    3. Handler calls VisionService
    4. Returns text response
    """

    # Patch the service inside the handler AND the ImageContent.from_file
    with (
        patch(
            "core.orchestration.handlers.vision.VisionService",
            return_value=mock_vision_service,
        ),
        patch("core.services.vision.models.ImageContent.from_file") as mock_from_file,
    ):
        # Setup mock image content
        mock_from_file.return_value = ImageContent(
            data="fake_base64", media_type="image/png", source_type="file"
        )

        handler = VisionHandler()

        context = {"image_paths": ["/tmp/fake_image.png"]}

        # Test basic analysis
        result = await handler.handle("Describe this image", context)

        assert result["response"] == "This is a mocked description of a cat."
        assert not result.get("error")

        # Verify service was called correctly
        assert mock_vision_service.analyze.called
        call_args = mock_vision_service.analyze.call_args
        request = call_args[0][0]
        assert len(request.images) == 1
        assert request.prompt == "Describe this image"


@pytest.mark.asyncio
async def test_vision_handler_no_image():
    """Verify error when no image is provided."""
    with patch("core.orchestration.handlers.vision.VisionService"):
        handler = VisionHandler()
        result = await handler.handle("Describe", {})
        assert result.get("error")
        assert "provide one" in result["response"]
