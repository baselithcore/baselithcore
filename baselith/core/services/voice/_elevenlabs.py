"""A minimal ElevenLabs client over the SSRF-hardened HTTP client.

ElevenLabs is not OpenAI-compatible, so it has its own wire adapter: ``xi-api-key``
auth, ``POST /v1/text-to-speech/{voice_id}`` (JSON in, audio out), ``POST /v1/speech-to-text``
(multipart in, JSON out, the Scribe models) and ``GET /v2/voices``.

Safety properties, the same as :mod:`baselith.core.services.voice._openai_wire` plus two:

* **One host.** The egress policy allows exactly the configured base URL's host, and a
  redirect is refused (a 3xx is an error, never followed), so the key cannot be steered to
  another host.
* **No echo.** A provider error body is read (bounded) only to pick a stable code out of
  a whitelist of ``detail.status`` values; nothing from it reaches a message or a log.

Request and response bodies are capped, one deadline covers the whole exchange (retries
included), and a 429 is retried a bounded number of times unless the account is out of quota.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from baselith.core.observability.logging import get_logger
from baselith.core.security.bounded_body import BOUNDED_ACCEPT_ENCODING, read_bounded
from baselith.core.security.http import create_hardened_async_client
from baselith.core.security.ssrf import SsrfError, SsrfPolicy
from baselith.core.services.voice._elevenlabs_errors import KNOWN_STATUS, Kind, classify
from baselith.core.services.voice._elevenlabs_payload import (
    REQUEST_ID,
    is_stitched,
    speech_payload,
)
from baselith.core.services.voice._http_providers import read_voice_body
from baselith.core.services.voice.errors import (
    VoiceEgressError,
    VoiceError,
    VoiceInputError,
    VoiceLimitError,
    VoiceNotFoundError,
    VoiceRateLimitedError,
    VoiceTimeoutError,
    VoiceUnavailableError,
    VoiceUpstreamError,
    safe_text,
)
from baselith.core.services.voice.models import SpeechContext, VoiceInfo

logger = get_logger(__name__)


#: What a voice id looks like; it goes into a URL path, so nothing else is let through.
VOICE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_ERROR_BODY_MAX = 4096
_RETRY_AFTER_MAX = 2.0
_PAGE_SIZE = 100
_MAX_PAGES = 3
_LANG_NAMES = {"italian": "it", "english": "en"}


@dataclass(frozen=True)
class ElevenLabsTarget:
    """One call's credential. The key is unwrapped only here, at the wire boundary."""

    api_key: str

    def secrets(self) -> tuple[str, ...]:
        return (self.api_key,)


@dataclass(frozen=True)
class SpeechResult:
    """One synthesised passage: the audio and what the provider told about the request."""

    audio: bytes
    request_id: str | None = None
    stitched: bool = False


def _language_codes(raw: Any) -> tuple[str, ...]:
    """Lower-case ISO 639-1 codes out of a voice's ``verified_languages``."""
    found: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        value = item.get("language") if isinstance(item, dict) else None
        if not isinstance(value, str):
            continue
        code = value.strip().lower().split("-")[0].split("_")[0]
        code = _LANG_NAMES.get(code, code)
        if 2 <= len(code) <= 3 and code.isalpha() and code not in found:
            found.append(code)
    return tuple(found)


def parse_voices(payload: Any) -> list[VoiceInfo]:
    """The usable voices of a ``/v2/voices`` page; malformed entries are skipped."""
    voices: list[VoiceInfo] = []
    items = payload.get("voices") if isinstance(payload, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        vid, name = item.get("voice_id"), item.get("name")
        if (
            not isinstance(vid, str)
            or not VOICE_ID.fullmatch(vid)
            or not isinstance(name, str)
        ):
            continue
        category = item.get("category")
        voices.append(
            VoiceInfo(
                id=vid,
                name=safe_text(name)[:80] or vid,
                languages=_language_codes(item.get("verified_languages")),
                category=category if isinstance(category, str) else "",
            )
        )
    return voices


#: How long an idle connection to the provider is kept for the next sentence.
_KEEPALIVE_SECONDS = 45.0


class ElevenLabsClient:
    """Capped, deadline-bound client for the ElevenLabs endpoints Lith needs."""

    def __init__(
        self,
        *,
        base_url: str,
        allow_internal: bool,
        timeout_seconds: float,
        max_request_bytes: int,
        max_response_bytes: int,
        retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_request = max_request_bytes
        self._max_response = max_response_bytes
        self._retries = max(0, retries)
        policy = SsrfPolicy(
            allow_internal=allow_internal,
            allowed_schemes=frozenset(
                {"https", "http"} if allow_internal else {"https"}
            ),
            allowed_hosts=frozenset({(parts.hostname or "").lower()}),
        )
        kwargs: dict[str, Any] = {"timeout": timeout_seconds, "follow_redirects": False}
        if transport is not None:
            kwargs["transport"] = transport
        else:
            # The hardened client disables keep-alive by default, so that one pool never coalesces
            # two validated hostnames behind a shared pinned IP. This policy admits exactly one
            # host, so the pool can only ever hold connections to it: keeping them costs no
            # isolation and saves a DNS lookup, a TCP and a TLS handshake on every sentence a
            # voice assistant speaks (the dominant part of its time to first audio).
            kwargs["limits"] = httpx.Limits(
                max_connections=20,
                max_keepalive_connections=4,
                keepalive_expiry=_KEEPALIVE_SECONDS,
            )
        self._client = create_hardened_async_client(policy, **kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- the calls ------------------------------------------------------------------------

    async def transcribe(
        self,
        target: ElevenLabsTarget,
        audio: bytes,
        *,
        model: str,
        filename: str = "audio.webm",
        content_type: str = "application/octet-stream",
        language: str | None = None,
    ) -> tuple[str, float | None]:
        """Transcribe ``audio``: the text (possibly empty) and the audio length, if told."""
        if not audio:
            raise VoiceInputError("audio is empty")
        if len(audio) > self._max_request:
            raise VoiceLimitError("audio exceeds the upload cap")
        # Scribe tags laughter and the like as "(laughter)" by default: not wanted in a composer.
        data = {"model_id": model, "tag_audio_events": "false", "diarize": "false"}
        if language:
            data["language_code"] = language
        body = await self._call(
            target,
            "POST",
            "/v1/speech-to-text",
            kind="stt",
            data=data,
            files={"file": (filename, audio, content_type)},
        )
        payload = self._json(body)
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            raise VoiceUpstreamError("the transcription response was not understood")
        secs = payload.get("audio_duration_secs")
        seconds = float(secs) if isinstance(secs, (int, float)) and secs > 0 else None
        return text.strip(), seconds

    async def speech(
        self,
        target: ElevenLabsTarget,
        text: str,
        *,
        voice_id: str,
        model: str,
        output_format: str,
        stability: float,
        similarity_boost: float,
        style: float,
        speed: float = 1.0,
        language: str | None = None,
        context: SpeechContext | None = None,
    ) -> bytes:
        """Synthesize ``text``; returns the audio bytes."""
        result = await self.speech_result(
            target,
            text,
            voice_id=voice_id,
            model=model,
            output_format=output_format,
            stability=stability,
            similarity_boost=similarity_boost,
            style=style,
            speed=speed,
            language=language,
            context=context,
        )
        return result.audio

    async def speech_result(
        self,
        target: ElevenLabsTarget,
        text: str,
        *,
        voice_id: str,
        model: str,
        output_format: str,
        stability: float,
        similarity_boost: float,
        style: float,
        speed: float = 1.0,
        language: str | None = None,
        context: SpeechContext | None = None,
    ) -> SpeechResult:
        """Synthesize ``text``: the audio, the provider's request id and whether it was stitched."""
        if not text.strip():
            raise VoiceInputError("text is empty")
        if not VOICE_ID.fullmatch(voice_id):
            raise VoiceNotFoundError("the voice id is not valid")
        payload = speech_payload(
            text,
            model=model,
            stability=stability,
            similarity_boost=similarity_boost,
            style=style,
            speed=speed,
            language=language,
            context=context,
        )
        audio, headers = await self._exchange(
            target,
            "POST",
            f"/v1/text-to-speech/{quote(voice_id, safe='')}",
            kind="tts",
            params={"output_format": output_format},
            json=payload,
        )
        if not audio:
            raise VoiceUpstreamError("the speech response was empty")
        raw = headers.get("request-id", "")
        return SpeechResult(
            audio=audio,
            request_id=raw if REQUEST_ID.fullmatch(raw) else None,
            stitched=is_stitched(payload),
        )

    async def voices(self, target: ElevenLabsTarget) -> list[VoiceInfo]:
        """The voices the account can use (a few pages at most)."""
        found: list[VoiceInfo] = []
        token: str | None = None
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {
                "page_size": _PAGE_SIZE,
                "include_total_count": "false",
            }
            if token:
                params["next_page_token"] = token
            payload = self._json(
                await self._call(
                    target, "GET", "/v2/voices", kind="voices", params=params
                )
            )
            found.extend(parse_voices(payload))
            token = (
                payload.get("next_page_token") if isinstance(payload, dict) else None
            )
            if not (isinstance(payload, dict) and payload.get("has_more") and token):
                break
        return found

    async def check(self, target: ElevenLabsTarget) -> None:
        """A cheap authenticated read (one voice); raises the classified error on failure."""
        await self._call(
            target,
            "GET",
            "/v2/voices",
            kind="voices",
            params={"page_size": 1, "include_total_count": "false"},
        )

    async def usage(self, target: ElevenLabsTarget) -> tuple[int, int] | None:
        """``(used, limit)`` characters this period, or None (no permission, or unparseable)."""
        try:
            payload = self._json(
                await self._call(target, "GET", "/v1/user/subscription", kind="voices")
            )
        except VoiceError:
            return None
        used, limit = payload.get("character_count"), payload.get("character_limit")
        if isinstance(used, int) and isinstance(limit, int) and limit > 0:
            return used, limit
        return None

    # -- the wire -------------------------------------------------------------------------

    @staticmethod
    def _json(body: bytes) -> Any:
        try:
            return json.loads(body)
        except ValueError:
            raise VoiceUpstreamError(
                "the voice provider answered with something unusable"
            ) from None

    async def _call(
        self,
        target: ElevenLabsTarget,
        method: str,
        path: str,
        *,
        kind: Kind,
        **kwargs: Any,
    ) -> bytes:
        body, _ = await self._exchange(target, method, path, kind=kind, **kwargs)
        return body

    async def _exchange(
        self,
        target: ElevenLabsTarget,
        method: str,
        path: str,
        *,
        kind: Kind,
        **kwargs: Any,
    ) -> tuple[bytes, httpx.Headers]:
        headers = {
            "xi-api-key": target.api_key,
            "Accept-Encoding": BOUNDED_ACCEPT_ENCODING,
        }
        url = self._base + path
        try:
            async with asyncio.timeout(self._timeout):
                for attempt in range(self._retries + 1):
                    request = self._client.build_request(
                        method, url, headers=headers, **kwargs
                    )
                    response = await self._client.send(request, stream=True)
                    try:
                        status = response.status_code
                        if 200 <= status < 300:
                            return await self._read(response), response.headers
                        if 300 <= status < 400:
                            logger.warning("voice_redirect_refused", status=status)
                            raise VoiceUnavailableError(
                                "the voice provider redirected the call"
                            )
                        detail = await self._detail(response)
                        error = classify(status, detail, kind)
                        if (
                            isinstance(error, VoiceRateLimitedError)
                            and attempt < self._retries
                        ):
                            pause = self._pause(response, attempt)
                        else:
                            raise error
                    finally:
                        await response.aclose()
                    await asyncio.sleep(pause)
        except SsrfError as exc:
            raise VoiceEgressError(safe_text(str(exc), target.secrets())) from None
        except (TimeoutError, httpx.TimeoutException):
            raise VoiceTimeoutError(
                "the voice provider did not answer in time"
            ) from None
        except httpx.HTTPError as exc:
            logger.warning("voice_transport_failed", error_type=type(exc).__name__)
            raise VoiceUnavailableError(
                "the voice provider could not be reached"
            ) from None
        raise VoiceRateLimitedError(
            "the voice provider is rate limiting"
        )  # pragma: no cover

    @staticmethod
    def _pause(response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("retry-after", "")
        try:
            wait = float(raw) if raw else 0.5 * (2**attempt)
        except ValueError:
            wait = 0.5 * (2**attempt)
        return min(_RETRY_AFTER_MAX, max(0.0, wait))

    async def _detail(self, response: httpx.Response) -> str | None:
        """The whitelisted ``detail.status`` of an error body; everything else is dropped."""
        try:
            # A BodyLimitError (too large, unbounded encoding) is a ValueError.
            body = await read_bounded(response, _ERROR_BODY_MAX)
            detail = json.loads(body).get("detail")
        except (ValueError, AttributeError, httpx.HTTPError):
            return None
        status = detail.get("status") if isinstance(detail, dict) else None
        return status if isinstance(status, str) and status in KNOWN_STATUS else None

    async def _read(self, response: httpx.Response) -> bytes:
        return await read_voice_body(response, self._max_response)


__all__ = [
    "ElevenLabsClient",
    "ElevenLabsTarget",
    "SpeechResult",
    "classify",
    "parse_voices",
]
