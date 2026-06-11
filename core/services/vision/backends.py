"""
Vision provider backends.

HTTP/SDK calls for each vision provider (OpenAI, Anthropic, Google, Ollama),
extracted from ``core/services/vision/service.py`` to keep modules under the
500-line cap. Each function receives the owning :class:`VisionService` for
access to resolved keys, model identifiers, and the shared HTTP client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.config import get_vision_config
from core.observability.logging import get_logger
from core.services.vision.models import (
    VisionProvider,
    VisionRequest,
    VisionResponse,
)

if TYPE_CHECKING:  # pragma: no cover
    from core.services.vision.service import VisionService

logger = get_logger(__name__)


async def analyze_openai(
    service: "VisionService", request: VisionRequest, prompt: str
) -> VisionResponse:
    """Analyze using OpenAI."""
    if not service._openai_key:
        raise ValueError("OpenAI API key not configured")

    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise ImportError("openai package required: pip install openai") from None

    client = AsyncOpenAI(api_key=service._openai_key)
    model = service.models[VisionProvider.OPENAI]

    # Build messages with images
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in request.images:
        content.append(image.to_openai_format())

    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        response_format={"type": "json_object"} if request.json_mode else None,
    )  # type: ignore[call-overload]

    return VisionResponse(
        success=True,
        content=response.choices[0].message.content or "",
        provider="openai",
        model=model,
        tokens_used=response.usage.total_tokens if response.usage else 0,
        raw_response=response.model_dump(),
    )


async def analyze_anthropic(
    service: "VisionService", request: VisionRequest, prompt: str
) -> VisionResponse:
    """Analyze using Anthropic."""
    if not service._anthropic_key:
        raise ValueError("Anthropic API key not configured")

    model = service.models[VisionProvider.ANTHROPIC]

    # Build content with images
    content: list[dict[str, Any]] = []
    for image in request.images:
        content.append(image.to_anthropic_format())
    content.append({"type": "text", "text": prompt})

    client = service._get_http_client()
    response = await client.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": service._anthropic_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": "user", "content": content}],
        },
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()

    return VisionResponse(
        success=True,
        content=data["content"][0]["text"],
        provider="anthropic",
        model=model,
        tokens_used=data.get("usage", {}).get("input_tokens", 0)
        + data.get("usage", {}).get("output_tokens", 0),
        raw_response=data,
    )


async def analyze_google(
    service: "VisionService", request: VisionRequest, prompt: str
) -> VisionResponse:
    """Analyze using Google Gemini."""
    if not service._google_key:
        raise ValueError("Google API key not configured")

    model = service.models[VisionProvider.GOOGLE]

    # Build parts with images
    parts: list[dict[str, Any]] = []
    for image in request.images:
        parts.append(
            {
                "inline_data": {
                    "mime_type": image.media_type,
                    "data": image.data,
                }
            }
        )
    parts.append({"text": prompt})

    client = service._get_http_client()
    response = await client.post(
        f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent",
        headers={"Content-Type": "application/json"},
        params={"key": service._google_key},
        json={
            "contents": [{"parts": parts}],
            "generationConfig": {
                "maxOutputTokens": request.max_tokens,
                "temperature": request.temperature,
            },
        },
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()

    content = data["candidates"][0]["content"]["parts"][0]["text"]

    return VisionResponse(
        success=True,
        content=content,
        provider="google",
        model=model,
        tokens_used=data.get("usageMetadata", {}).get("totalTokenCount", 0),
        raw_response=data,
    )


async def analyze_ollama(
    service: "VisionService", request: VisionRequest, prompt: str
) -> VisionResponse:
    """Analyze using Ollama (local)."""
    _cfg = get_vision_config()
    model = service.models[VisionProvider.OLLAMA]
    ollama_url = _cfg.ollama_url

    # Ollama expects images as base64 array
    images = [img.data for img in request.images if img.source_type == "base64"]

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "images": images,
        "stream": False,
    }
    if request.json_mode:
        payload["format"] = "json"
    client = service._get_http_client()
    response = await client.post(
        f"{ollama_url}/api/generate",
        json=payload,
        timeout=180.0,
    )
    response.raise_for_status()
    data = response.json()

    return VisionResponse(
        success=True,
        content=data.get("response", ""),
        provider="ollama",
        model=model,
        tokens_used=data.get("eval_count", 0),
        raw_response=data,
    )


__all__ = [
    "analyze_anthropic",
    "analyze_google",
    "analyze_ollama",
    "analyze_openai",
]
