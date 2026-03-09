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

from core.services.vision.service import VisionService
from core.services.vision.models import (
    VisionRequest,
    VisionResponse,
    ImageContent,
    VisionCapability,
)

__all__ = [
    "VisionService",
    "VisionRequest",
    "VisionResponse",
    "ImageContent",
    "VisionCapability",
]
