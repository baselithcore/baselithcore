"""
Vision Service Module.

Provides multi-modal vision capabilities using various LLM providers
(OpenAI GPT-4o, Anthropic Claude, Google Gemini).

Enables agents to "see" and analyze:
- Images and screenshots
- PDF documents (passed natively to capable providers)
- Audio clips (passed natively to capable providers)
- Diagrams and architectures
- UI elements for testing
"""

from core.services.vision.media_models import (
    AudioContent,
    DocumentContent,
    UnsupportedContentError,
)
from core.services.vision.models import (
    ImageContent,
    VisionCapability,
    VisionRequest,
    VisionResponse,
)
from core.services.vision.service import VisionService

__all__ = [
    "AudioContent",
    "DocumentContent",
    "ImageContent",
    "UnsupportedContentError",
    "VisionCapability",
    "VisionRequest",
    "VisionResponse",
    "VisionService",
]
