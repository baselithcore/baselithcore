"""
Vision Models.

Data models for vision service requests and responses.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal


class VisionCapability(str, Enum):
    """Vision capabilities supported by the service."""

    IMAGE_ANALYSIS = "image_analysis"
    OCR = "ocr"
    DIAGRAM_UNDERSTANDING = "diagram_understanding"
    SCREENSHOT_ANALYSIS = "screenshot_analysis"
    OBJECT_DETECTION = "object_detection"
    COMPARISON = "comparison"


class VisionProvider(str, Enum):
    """Supported vision providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OLLAMA = "ollama"


@dataclass
class ImageContent:
    """
    Represents an image for vision analysis.

    Can be created from:
    - Base64-encoded data
    - File path
    - URL
    """

    data: str  # Base64 encoded image data
    media_type: str = "image/png"
    source_type: Literal["base64", "url", "file"] = "base64"

    @classmethod
    def from_base64(cls, data: str, media_type: str = "image/png") -> ImageContent:
        """
        Create an ImageContent instance from a base64 string.

        Args:
            data: The base64-encoded image data.
            media_type: The MIME type (default: image/png).

        Returns:
            ImageContent: A new image content instance.
        """
        return cls(data=data, media_type=media_type, source_type="base64")

    @classmethod
    def from_file(cls, path: str | Path) -> ImageContent:
        """
        Load an image from a local file and encode it as base64.

        Args:
            path: Absolute or relative path to the image file.

        Returns:
            ImageContent: Encoded image data with detected media type.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Image file not found: {path}")

        # Determine media type from extension
        ext_to_mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        media_type = ext_to_mime.get(file_path.suffix.lower(), "image/png")

        # Read and encode
        with open(file_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")

        return cls(data=data, media_type=media_type, source_type="file")

    @classmethod
    def from_url(cls, url: str) -> ImageContent:
        """
        Create an ImageContent instance referencing a remote URL.

        Args:
            url: Publicly accessible URL of the image.

        Returns:
            ImageContent: A URL-referenced image content instance.
        """
        return cls(data=url, media_type="image/url", source_type="url")

    def to_openai_format(self) -> dict[str, Any]:
        """
        Format the image for the OpenAI Vision API.

        Returns:
            dict[str, Any]: OpenAI-compatible message content block.
        """
        if self.source_type == "url":
            return {"type": "image_url", "image_url": {"url": self.data}}
        else:
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{self.media_type};base64,{self.data}"},
            }

    def to_anthropic_format(self) -> dict[str, Any]:
        """
        Format the image for the Anthropic Claude Vision API.

        Returns:
            dict[str, Any]: Anthropic-compatible message content block.
        """
        if self.source_type == "url":
            return {"type": "image", "source": {"type": "url", "url": self.data}}
        else:
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": self.media_type,
                    "data": self.data,
                },
            }


@dataclass
class VisionRequest:
    """
    Request for vision analysis.

    Supports single or multiple images with a text prompt.
    """

    prompt: str
    images: list[ImageContent] = field(default_factory=list)
    capability: VisionCapability = VisionCapability.IMAGE_ANALYSIS
    max_tokens: int = 1024
    temperature: float = 0.0
    json_mode: bool = False
    provider: VisionProvider | None = None  # None = use default

    def add_image(self, image: ImageContent) -> VisionRequest:
        """Add an image to the request (fluent API)."""
        self.images.append(image)
        return self

    def add_image_from_file(self, path: str | Path) -> VisionRequest:
        """Add an image from a file path."""
        self.images.append(ImageContent.from_file(path))
        return self

    def add_image_from_url(self, url: str) -> VisionRequest:
        """Add an image from a URL."""
        self.images.append(ImageContent.from_url(url))
        return self


@dataclass
class VisionResponse:
    """Response from vision analysis."""

    success: bool
    content: str
    provider: str
    model: str
    tokens_used: int = 0
    raw_response: dict[str, Any] = field(default_factory=dict)

    @property
    def as_json(self) -> dict[str, Any] | None:
        """Try to parse content as JSON."""
        import json

        try:
            return json.loads(self.content)
        except json.JSONDecodeError:
            return None
