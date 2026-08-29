"""Image file loading must run off the event loop.

``ImageContent.from_file`` does synchronous ``open()`` + base64 of a whole
image; the vision and multimodal handlers used to call it serially inside
``async def``, blocking the loop for the duration of every disk read.
"""

from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock

from core.orchestration.handlers.multimodal_reasoning import MultiModalReasoningHandler
from core.services.vision.models import ImageContent


def _tracking_from_file(loaded: list[int]):
    def _fake(path):
        loaded.append(threading.get_ident())
        return ImageContent(data="ZmFrZQ==", media_type="image/png")

    return _fake


async def test_multimodal_extract_images_loads_files_off_loop(monkeypatch):
    handler = MultiModalReasoningHandler(
        vision_service=MagicMock(analyze=AsyncMock()),
        llm_service=MagicMock(),
    )
    loaded: list[int] = []
    monkeypatch.setattr(
        "core.orchestration.handlers.multimodal_reasoning.ImageContent.from_file",
        _tracking_from_file(loaded),
    )

    images = await handler._extract_images(
        {"image_paths": ["/a.png", "/b.png"], "image_data": [], "image_urls": []}
    )

    assert len(images) == 2
    assert loaded and all(ident != threading.get_ident() for ident in loaded)


async def test_multimodal_extract_images_tolerates_failures(monkeypatch):
    handler = MultiModalReasoningHandler(
        vision_service=MagicMock(analyze=AsyncMock()),
        llm_service=MagicMock(),
    )

    def _boom(path):
        raise OSError("unreadable")

    monkeypatch.setattr(
        "core.orchestration.handlers.multimodal_reasoning.ImageContent.from_file",
        _boom,
    )

    images = await handler._extract_images({"image_paths": ["/broken.png"]})
    assert images == []


async def test_vision_handler_loads_files_off_loop(monkeypatch):
    from core.orchestration.handlers.vision import VisionHandler

    handler = VisionHandler()
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(
        return_value=MagicMock(
            success=True, content="ok", provider="openai", model="m", tokens_used=1
        )
    )
    loaded: list[int] = []
    monkeypatch.setattr(
        "core.orchestration.handlers.vision.ImageContent.from_file",
        _tracking_from_file(loaded),
    )

    result = await handler.handle("describe", {"image_paths": ["/a.png"]})

    assert not result.get("error")
    assert loaded and all(ident != threading.get_ident() for ident in loaded)
