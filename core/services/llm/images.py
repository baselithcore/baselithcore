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

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from core.observability.logging import get_logger
from core.services.llm.exceptions import LLMProviderError
from core.utils.images import sniff_image_type

logger = get_logger(__name__)

__all__ = [
    "GeneratedImage",
    "SupportsImageGeneration",
    "decode_image_payload",
    "generate_image",
    "sniff_image_type",
]

#: ``data:image/png;base64,`` and friends. The APIs return bare base64, but a
#: gateway in front of one may hand back a data URL instead.
_DATA_URL = re.compile(r"^data:[^;,]*;base64,", re.IGNORECASE)


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


def decode_image_payload(payload: str) -> tuple[bytes, str]:
    """Turn a provider's base64 image payload into bytes and their real type.

    Two things go wrong here and neither raises on its own. ``b64decode``
    silently ignores characters outside the alphabet, so a ``data:…;base64,``
    prefix decodes into a corrupt image instead of failing — the caller stores
    it, the console serves it, and the defect only shows up as a broken
    ``<img>`` in a review. And a provider's declared format is an assumption:
    the type is read from the bytes.

    Args:
        payload: Base64 text, with or without a data-URL prefix and line wraps.

    Returns:
        ``(data, media_type)`` — the decoded bytes and their IANA type.

    Raises:
        LLMProviderError: The payload is not valid base64, or the bytes are not
            an image this framework recognises (PNG, JPEG, GIF, WebP).
    """
    cleaned = "".join(_DATA_URL.sub("", payload.strip()).split())
    try:
        data = base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LLMProviderError(
            f"the provider's image payload is not valid base64: {exc}"
        ) from exc
    media_type = sniff_image_type(data)
    if media_type is None:
        # The head, not the whole body: an error page rendered as an image is
        # the usual cause, and its first bytes say which one.
        head = data[:16].hex(" ") or "(empty)"
        raise LLMProviderError(
            f"the provider returned {len(data)} bytes that are not an image "
            f"this framework recognises (first bytes: {head})"
        )
    return data, media_type


@runtime_checkable
class SupportsImageGeneration(Protocol):
    """Providers that can generate images implement this."""

    async def generate_image(
        self,
        prompt: str,
        *,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        **kwargs: Any,
    ) -> GeneratedImage:  # pragma: no cover — structural type
        ...


async def generate_image(
    service: Any,
    prompt: str,
    *,
    model: str | None = None,
    size: str | None = None,
    quality: str | None = None,
) -> GeneratedImage:
    """Generate one image through the service's active provider.

    Args:
        service: The owning ``LLMService``.
        prompt: What to draw. Providers treat this as the whole brief, so it
            should describe subject, composition and mood, not just a topic.
        model: Image model override; the provider's default when None.
        size: Provider-specific size string, e.g. ``1536x1024``.
        quality: Provider-specific quality tier (``low``/``medium``/``high``
            for the GPT image models); the provider's default when None. The
            main cost lever — the default tier bills several times a ``low``
            render of the same size.

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
    image = await provider.generate_image(
        prompt, model=model, size=size, quality=quality
    )
    logger.info(
        "llm_image_generated",
        model=image.model,
        media_type=image.media_type,
        bytes=len(image.data),
    )
    return image
