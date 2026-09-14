"""Image generation for the OpenAI provider.

Split out of ``openai_provider`` for the module size cap: images share the
client and the error taxonomy with chat, and nothing else — no prompt
shaping, no usage accounting, no tool calling.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger
from core.services.llm.errors import map_provider_exception
from core.services.llm.exceptions import LLMProviderError, describe_exception
from core.services.llm.images import GeneratedImage, decode_image_payload

logger = get_logger(__name__)

__all__ = ["DEFAULT_IMAGE_MODEL", "DEFAULT_IMAGE_SIZE", "generate_image"]

# Landscape by default: every consumer so far wants a cover, not a square.
DEFAULT_IMAGE_MODEL = "gpt-image-1"
DEFAULT_IMAGE_SIZE = "1536x1024"


async def generate_image(
    client: Any,
    prompt: str,
    *,
    model: str | None = None,
    size: str | None = None,
    quality: str | None = None,
    **kwargs: Any,
) -> GeneratedImage:
    """Generate one image and return its bytes.

    Args:
        client: The initialised AsyncOpenAI client.
        prompt: The whole brief for the image.
        model: Image model; ``gpt-image-1`` when None.
        size: Provider size string; a landscape cover when None.
        quality: Quality tier (``low``/``medium``/``high`` for the GPT image
            models). Omitted from the request when None, because the accepted
            values differ per model (``dall-e-3`` takes ``standard``/``hd``)
            and the API rejects unknown ones.
        **kwargs: Passthrough parameters.

    Returns:
        The decoded image and the model that produced it.

    Raises:
        LLMProviderError: The API refused the request, or returned a payload
            with no image in it.
    """
    chosen = model or DEFAULT_IMAGE_MODEL
    if quality is not None:
        kwargs["quality"] = quality
    try:
        response = await client.images.generate(
            model=chosen,
            prompt=prompt,
            size=size or DEFAULT_IMAGE_SIZE,
            n=1,
            **kwargs,
        )
        item = (response.data or [None])[0]
        payload = getattr(item, "b64_json", None) if item else None
        if not payload:
            raise LLMProviderError(
                "OpenAI returned no image data (the model may return a URL "
                "instead of base64 — this provider expects base64)"
            )
        # Not a bare b64decode: a data-URL prefix would decode into a corrupt
        # image without raising, and the returned format is not always the
        # documented PNG. Both are read off the bytes.
        data, media_type = decode_image_payload(payload)
        return GeneratedImage(
            data=data,
            media_type=media_type,
            model=chosen,
            revised_prompt=getattr(item, "revised_prompt", None),
        )
    except LLMProviderError:
        raise
    except Exception as e:
        logger.error(f"OpenAI image generation error: {describe_exception(e)}")
        raise map_provider_exception(e, provider="OpenAI") from e
