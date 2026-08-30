"""Document and audio provider backends.

Native document/audio calls per provider, sibling to
:mod:`core.services.vision.backends` (kept separate to stay under the
500-line cap). Support matrix:

===========  ==============  ==============
Provider     PDF             Audio
===========  ==============  ==============
Anthropic    native          unsupported
Google       native          native
OpenAI       unsupported     native (wav/mp3)
Ollama       unsupported     unsupported
===========  ==============  ==============

Unsupported combinations raise
:class:`~core.services.vision.media_models.UnsupportedContentError`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.services.vision.media_models import (
    AudioContent,
    DocumentContent,
    UnsupportedContentError,
)
from core.services.vision.models import VisionProvider, VisionResponse

if TYPE_CHECKING:  # pragma: no cover
    from core.services.vision.service import VisionService

logger = get_logger(__name__)

_MAX_TOKENS = 1024


async def analyze_document_anthropic(
    service: VisionService, document: DocumentContent, prompt: str
) -> VisionResponse:
    """Analyze a document natively via Anthropic's ``document`` block."""
    if not service._anthropic_key:
        raise ValueError("Anthropic API key not configured")

    model = service.models[VisionProvider.ANTHROPIC]
    content: list[dict[str, Any]] = [
        document.to_anthropic_format(),
        {"type": "text", "text": prompt},
    ]

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
            "max_tokens": _MAX_TOKENS,
            "messages": [{"role": "user", "content": content}],
        },
        timeout=120.0,
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


async def analyze_document_google(
    service: VisionService, document: DocumentContent, prompt: str
) -> VisionResponse:
    """Analyze a document natively via a Gemini ``inline_data``/``file_data`` part."""
    parts = [document.to_google_part(), {"text": prompt}]
    return await _google_generate(service, parts)


async def analyze_document_openai(
    service: VisionService, document: DocumentContent, prompt: str
) -> VisionResponse:
    """Reject documents for OpenAI: no native path on chat completions."""
    raise UnsupportedContentError(
        "openai",
        document.media_type,
        f"OpenAI's chat-completions path has no native {document.media_type!r} "
        "content block.",
    )


async def analyze_document_ollama(
    service: VisionService, document: DocumentContent, prompt: str
) -> VisionResponse:
    """Reject documents for Ollama: no native document input."""
    raise UnsupportedContentError("ollama", document.media_type)


async def analyze_audio_openai(
    service: VisionService, audio: AudioContent, prompt: str
) -> VisionResponse:
    """Analyze audio natively via OpenAI's ``input_audio`` content part."""
    if not service._openai_key:
        raise ValueError("OpenAI API key not configured")

    # Raises UnsupportedContentError for formats input_audio cannot take.
    audio_part = audio.to_openai_format()
    # The vision model (gpt-4o) cannot take input_audio: use the audio model.
    model = service.openai_audio_model

    client = service._get_openai_client()
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}, audio_part],
            }
        ],
        max_tokens=_MAX_TOKENS,
    )

    return VisionResponse(
        success=True,
        content=response.choices[0].message.content or "",
        provider="openai",
        model=model,
        tokens_used=response.usage.total_tokens if response.usage else 0,
        raw_response=response.model_dump(),
    )


async def analyze_audio_google(
    service: VisionService, audio: AudioContent, prompt: str
) -> VisionResponse:
    """Analyze audio natively via a Gemini ``inline_data`` part."""
    parts = [audio.to_google_part(), {"text": prompt}]
    return await _google_generate(service, parts)


async def analyze_audio_anthropic(
    service: VisionService, audio: AudioContent, prompt: str
) -> VisionResponse:
    """Reject audio for Anthropic: the Messages API has no audio block."""
    raise UnsupportedContentError("anthropic", audio.media_type)


async def analyze_audio_ollama(
    service: VisionService, audio: AudioContent, prompt: str
) -> VisionResponse:
    """Reject audio for Ollama: no native audio input."""
    raise UnsupportedContentError("ollama", audio.media_type)


async def _google_generate(
    service: VisionService, parts: list[dict[str, Any]]
) -> VisionResponse:
    """POST ``generateContent`` parts to Gemini and unwrap the first candidate."""
    if not service._google_key:
        raise ValueError("Google API key not configured")

    model = service.models[VisionProvider.GOOGLE]
    client = service._get_http_client()
    response = await client.post(
        f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent",
        headers={"Content-Type": "application/json"},
        params={"key": service._google_key},
        json={
            "contents": [{"parts": parts}],
            "generationConfig": {"maxOutputTokens": _MAX_TOKENS},
        },
        timeout=120.0,
    )
    response.raise_for_status()
    data = response.json()

    return VisionResponse(
        success=True,
        content=data["candidates"][0]["content"]["parts"][0]["text"],
        provider="google",
        model=model,
        tokens_used=data.get("usageMetadata", {}).get("totalTokenCount", 0),
        raw_response=data,
    )


_DocumentBackend = Callable[
    ["VisionService", DocumentContent, str], Awaitable[VisionResponse]
]
_AudioBackend = Callable[
    ["VisionService", AudioContent, str], Awaitable[VisionResponse]
]

_DOCUMENT_BACKENDS: dict[VisionProvider, _DocumentBackend] = {
    VisionProvider.ANTHROPIC: analyze_document_anthropic,
    VisionProvider.GOOGLE: analyze_document_google,
    VisionProvider.OPENAI: analyze_document_openai,
    VisionProvider.OLLAMA: analyze_document_ollama,
}

_AUDIO_BACKENDS: dict[VisionProvider, _AudioBackend] = {
    VisionProvider.ANTHROPIC: analyze_audio_anthropic,
    VisionProvider.GOOGLE: analyze_audio_google,
    VisionProvider.OPENAI: analyze_audio_openai,
    VisionProvider.OLLAMA: analyze_audio_ollama,
}


async def analyze_document(
    service: VisionService,
    document: DocumentContent,
    prompt: str,
    provider: VisionProvider,
) -> VisionResponse:
    """Dispatch a document analysis to the given provider's backend."""
    backend = _DOCUMENT_BACKENDS.get(provider)
    if backend is None:
        raise ValueError(f"Unsupported provider: {provider}")
    return await backend(service, document, prompt)


async def analyze_audio(
    service: VisionService,
    audio: AudioContent,
    prompt: str,
    provider: VisionProvider,
) -> VisionResponse:
    """Dispatch an audio analysis to the given provider's backend."""
    backend = _AUDIO_BACKENDS.get(provider)
    if backend is None:
        raise ValueError(f"Unsupported provider: {provider}")
    return await backend(service, audio, prompt)


__all__ = [
    "analyze_audio",
    "analyze_audio_anthropic",
    "analyze_audio_google",
    "analyze_audio_ollama",
    "analyze_audio_openai",
    "analyze_document",
    "analyze_document_anthropic",
    "analyze_document_google",
    "analyze_document_ollama",
    "analyze_document_openai",
]
