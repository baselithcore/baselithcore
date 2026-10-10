"""A minimal OpenAI-audio-API client over the SSRF-hardened HTTP client.

Speaks ``POST {base}/audio/transcriptions`` (multipart) and
``POST {base}/audio/speech`` (JSON), which is what OpenAI itself, faster-whisper-server
and the Piper/Kokoro OpenAI-compatible wrappers serve. ``base`` is the API root and
includes ``/v1``.

Safety properties: every request, redirect hop included, passes
:func:`baselith.core.security.http.create_hardened_async_client` (internal addresses are
refused unless the caller opts in); the request body and the response body are
capped (the response is streamed and abandoned at the cap); one deadline covers the
whole exchange; and no exception message carries a response body.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from baselith.core.observability.logging import get_logger
from baselith.core.security.http import create_hardened_async_client
from baselith.core.security.ssrf import SsrfError, SsrfPolicy
from baselith.core.services.voice.errors import (
    VoiceEgressError,
    VoiceInputError,
    VoiceLimitError,
    VoiceTimeoutError,
    VoiceUpstreamError,
    safe_text,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class WireTarget:
    """Where one call goes: the API root and the bearer key, if any."""

    base_url: str
    api_key: str | None = None

    def secrets(self) -> tuple[str, ...]:
        return (self.api_key,) if self.api_key else ()


class OpenAIWireClient:
    """Capped, deadline-bound client for the OpenAI audio endpoints."""

    def __init__(
        self,
        *,
        allow_internal: bool,
        timeout_seconds: float,
        max_request_bytes: int,
        max_response_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_request = max_request_bytes
        self._max_response = max_response_bytes
        kwargs: dict[str, Any] = {"timeout": timeout_seconds}
        if transport is not None:
            kwargs["transport"] = transport
        self._client = create_hardened_async_client(
            SsrfPolicy(allow_internal=allow_internal), **kwargs
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def transcribe(
        self,
        target: WireTarget,
        audio: bytes,
        *,
        model: str,
        filename: str = "audio.webm",
        content_type: str = "application/octet-stream",
        language: str | None = None,
    ) -> str:
        """Transcribe ``audio``; returns the text (possibly empty)."""
        if not audio:
            raise VoiceInputError("audio is empty")
        if len(audio) > self._max_request:
            raise VoiceLimitError("audio exceeds the upload cap")
        data = {"model": model, "response_format": "json"}
        if language:
            data["language"] = language
        body = await self._post(
            target,
            "/audio/transcriptions",
            data=data,
            files={"file": (filename, audio, content_type)},
        )
        try:
            text = httpx.Response(200, content=body).json().get("text")
        except (ValueError, AttributeError):
            text = None
        if not isinstance(text, str):
            raise VoiceUpstreamError("the transcription response was not understood")
        return text.strip()

    async def speech(
        self,
        target: WireTarget,
        text: str,
        *,
        model: str,
        voice: str,
        fmt: str = "mp3",
        speed: float = 1.0,
    ) -> bytes:
        """Synthesize ``text``; returns the audio bytes."""
        if not text.strip():
            raise VoiceInputError("text is empty")
        audio = await self._post(
            target,
            "/audio/speech",
            json={
                "model": model,
                "voice": voice,
                "input": text,
                "response_format": fmt,
                "speed": speed,
            },
        )
        if not audio:
            raise VoiceUpstreamError("the speech response was empty")
        return audio

    async def _post(self, target: WireTarget, path: str, **kwargs: Any) -> bytes:
        headers = (
            {"Authorization": f"Bearer {target.api_key}"} if target.api_key else {}
        )
        url = target.base_url.rstrip("/") + path
        secrets = target.secrets()
        try:
            async with asyncio.timeout(self._timeout):
                request = self._client.build_request(
                    "POST", url, headers=headers, **kwargs
                )
                response = await self._client.send(request, stream=True)
                try:
                    if response.status_code != 200:
                        raise self._status_error(response.status_code)
                    return await self._read(response)
                finally:
                    await response.aclose()
        except SsrfError as exc:
            raise VoiceEgressError(safe_text(str(exc), secrets)) from None
        except (TimeoutError, httpx.TimeoutException):
            raise VoiceTimeoutError("the voice server did not answer in time") from None
        except httpx.HTTPError as exc:
            logger.warning("voice_transport_failed", error_type=type(exc).__name__)
            raise VoiceUpstreamError("the voice server could not be reached") from None

    async def _read(self, response: httpx.Response) -> bytes:
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self._max_response:
            raise VoiceLimitError("the response exceeds the size cap")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > self._max_response:
                raise VoiceLimitError("the response exceeds the size cap")
        return bytes(body)

    @staticmethod
    def _status_error(status: int) -> VoiceUpstreamError | VoiceLimitError:
        if status == 413:
            return VoiceLimitError("the voice server refused the request as too large")
        if status in (401, 403):
            return VoiceUpstreamError(
                "the voice server rejected the credentials", status=status
            )
        if status == 429:
            return VoiceUpstreamError(
                "the voice server is rate limiting requests", status=status
            )
        return VoiceUpstreamError(
            f"the voice server answered HTTP {status}", status=status
        )


__all__ = ["OpenAIWireClient", "WireTarget"]
