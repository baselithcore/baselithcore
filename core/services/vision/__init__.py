"""
Vision Service Module.

Provides multi-modal vision capabilities using various LLM providers
(OpenAI GPT-4o, Anthropic Claude, Google Gemini).

Enables agents to "see" and analyze:
- Images and screenshots
- PDF documents
- Diagrams and architectures
- UI elements for testing
"""

from core.services.vision.models import (
    ImageContent,
    VisionCapability,
    VisionRequest,
    VisionResponse,
)
from core.services.vision.service import VisionService

__all__ = [
    "ImageContent",
    "VisionCapability",
    "VisionRequest",
    "VisionResponse",
    "VisionService",
]
