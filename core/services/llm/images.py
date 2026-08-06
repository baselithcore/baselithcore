"""Image generation through the same provider layer as text.

A generated illustration is an ordinary model call: a prompt goes out, bytes
come back, and the caller wants neither the provider SDK nor the base64
plumbing in its own code. This is the seam for it, next to
:func:`core.services.llm.structured.generate_typed` and shaped the same way —
the provider is selected by the service, the caller passes intent.

Bytes, not a URL: a hosted image expires, and every consumer would otherwise
re-implement the download. What a caller does with the bytes (commit them,
store them, serve them) is its own business.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from core.observability.logging import get_logger
from core.services.llm.exceptions import LLMProviderError

logger = get_logger(__name__)

__all__ = ["GeneratedImage", "SupportsImageGeneration", "generate_image"]


@dataclass(slots=True)
class GeneratedImage:
    """One generated image and what produced it.

    Attributes:
        data: The raw image bytes.
        media_type: IANA type, e.g. ``image/png``.
        model: The model that produced it, for the record.
        revised_prompt: The prompt the provider actually used, when it
            rewrites one. Worth keeping: it explains an image that does not
            match the brief.
    """

    data: bytes
    media_type: str
    model: str
    revised_prompt: str | None = None


@runtime_checkable
class SupportsImageGeneration(Protocol):
    """Providers that can generate images implement this."""

    async def generate_image(
        self,
        prompt: str,
        *,
        model: str | None = None,
        size: str | None = None,
        **kwargs: Any,
    ) -> GeneratedImage:  # pragma: no cover — structural type
        ...


async def generate_image(
    service: Any,
    prompt: str,
    *,
    model: str | None = None,
    size: str | None = None,
) -> GeneratedImage:
    """Generate one image through the service's active provider.

    Args:
        service: The owning ``LLMService``.
        prompt: What to draw. Providers treat this as the whole brief, so it
            should describe subject, composition and mood, not just a topic.
        model: Image model override; the provider's default when None.
        size: Provider-specific size string, e.g. ``1536x1024``.

    Returns:
        The image bytes and their media type.

    Raises:
        LLMProviderError: The active provider cannot generate images, or the
            generation failed.
    """
    provider = getattr(service, "provider", None)
    if provider is None or not hasattr(provider, "generate_image"):
        name = type(provider).__name__ if provider is not None else "none"
        raise LLMProviderError(
            f"the active LLM provider ({name}) cannot generate images"
        )
    image = await provider.generate_image(prompt, model=model, size=size)
    logger.info(
        "llm_image_generated",
        model=image.model,
        media_type=image.media_type,
        bytes=len(image.data),
    )
    return image
