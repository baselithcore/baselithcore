"""The plain-HTTP provider paths of ``VoiceService`` (Google Cloud).

Same safety properties as the OpenAI-wire and ElevenLabs clients: one
``VOICE_TIMEOUT_SECONDS`` deadline per call, the response streamed and
abandoned at ``VOICE_MAX_RESPONSE_BYTES``, and a failure that never embeds the
response body. The API key travels in the ``X-Goog-Api-Key`` header, never in
the query string: as a ``?key=`` parameter it was part of the request URL, and
the URL is what ``httpx`` puts into every ``HTTPStatusError`` message — which
the service then logged on each 4xx/5xx.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import orjson

from baselith.core.observability.logging import get_logger
from baselith.core.services.voice.errors import (
    VoiceLimitError,
    VoiceTimeoutError,
    VoiceUpstreamError,
)
from baselith.core.services.voice.models import STTRequest, TTSRequest, VoiceResponse

logger = get_logger(__name__)

_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
_STT_URL = "https://speech.googleapis.com/v1/speech:recognize"


class HttpProviders:
    """Mixin of ``VoiceService``: TTS/STT over the shared ``httpx`` client."""

    def _config(self) -> Any:  # pragma: no cover - provided by VoiceService
        raise NotImplementedError

    def _get_http_client(self) -> Any:  # pragma: no cover - provided by VoiceService
        raise NotImplementedError

    def _google_key(self) -> str:
        secret = self._config().google_api_key
        if not secret:
            raise ValueError("Google API key not configured")
        key: str = secret.get_secret_value()
        return key

    async def _google_post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST ``payload`` to a Google API under the voice deadline and response cap."""
        import httpx

        cfg = self._config()
        client = self._get_http_client()
        headers = {"X-Goog-Api-Key": self._google_key()}
        max_bytes = int(cfg.max_response_bytes)
        try:
            async with asyncio.timeout(float(cfg.timeout_seconds)):
                request = client.build_request(
                    "POST", url, headers=headers, json=payload
                )
                response = await client.send(request, stream=True)
                try:
                    if response.status_code != 200:
                        raise VoiceUpstreamError(
                            f"the voice server answered HTTP {response.status_code}",
                            status=int(response.status_code),
                        )
                    body = await _read_capped(response, max_bytes)
                finally:
                    await response.aclose()
        except (TimeoutError, httpx.TimeoutException):
            raise VoiceTimeoutError("the voice server did not answer in time") from None
        except httpx.HTTPError as exc:
            logger.warning("voice_transport_failed", error_type=type(exc).__name__)
            raise VoiceUpstreamError("the voice server could not be reached") from None
        try:
            data = orjson.loads(body)
        except orjson.JSONDecodeError:
            raise VoiceUpstreamError(
                "the voice server answered with invalid JSON"
            ) from None
        if not isinstance(data, dict):
            raise VoiceUpstreamError(
                "the voice server answered with an unexpected shape"
            )
        return data

    async def _tts_google(self, request: TTSRequest) -> VoiceResponse:
        """
        Generate speech using Google Cloud Text-to-Speech.

        Args:
            request: Audio generation parameters.

        Returns:
            VoiceResponse: Generated audio data.
        """
        # Parse voice name for language code
        language_code = "en-US"
        if "-" in request.voice:
            parts = request.voice.split("-")
            if len(parts) >= 2:
                language_code = f"{parts[0]}-{parts[1]}"

        data = await self._google_post(
            _TTS_URL,
            {
                "input": {"text": request.text},
                "voice": {
                    "languageCode": language_code,
                    "name": request.voice,
                },
                "audioConfig": {
                    "audioEncoding": "MP3",
                    "speakingRate": request.speed,
                },
            },
        )
        encoded = data.get("audioContent")
        if not isinstance(encoded, str) or not encoded:
            raise VoiceUpstreamError("the speech response was empty")
        audio_bytes = base64.b64decode(encoded)

        return VoiceResponse(
            success=True,
            content=audio_bytes,
            provider="google",
            model="wavenet",
            metadata={"voice": request.voice, "language": language_code},
        )

    async def _stt_google(self, request: STTRequest) -> VoiceResponse:
        """
        Transcribe audio using Google Cloud Speech-to-Text.

        Args:
            request: Transcription parameters.

        Returns:
            VoiceResponse: Transcribed text.
        """
        audio_b64 = base64.b64encode(request.get_audio_bytes()).decode("utf-8")
        data = await self._google_post(
            _STT_URL,
            {
                "config": {
                    "encoding": "MP3",
                    "languageCode": request.language or "en-US",
                    "enableAutomaticPunctuation": True,
                },
                "audio": {"content": audio_b64},
            },
        )

        # Extract transcript
        results = data.get("results", [])
        transcript = " ".join(
            r["alternatives"][0]["transcript"]
            for r in results
            if isinstance(r, dict) and r.get("alternatives")
        )

        return VoiceResponse(
            success=True,
            content=transcript,
            provider="google",
            model="speech-v1",
            language=request.language,
        )


async def _read_capped(response: Any, max_bytes: int) -> bytes:
    """Read a streamed body, abandoning it as soon as it crosses ``max_bytes``."""
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise VoiceLimitError("the response exceeds the size cap")
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > max_bytes:
            raise VoiceLimitError("the response exceeds the size cap")
    return bytes(body)


__all__ = ["HttpProviders"]
