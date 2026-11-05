"""A minimal ElevenLabs client over the SSRF-hardened HTTP client.

ElevenLabs is not OpenAI-compatible, so it has its own wire adapter: ``xi-api-key``
auth, ``POST /v1/text-to-speech/{voice_id}`` (JSON in, audio out), its ``/stream`` twin (audio
out as it is generated), ``POST /v1/speech-to-text`` (multipart in, JSON out, the Scribe
models) and ``GET /v2/voices`` (:mod:`baselith.core.services.voice._elevenlabs_catalog`).

Safety properties, the same as :mod:`baselith.core.services.voice._openai_wire` plus two:

* **One host.** The egress policy allows exactly the configured base URL's host, and a
  redirect is refused (a 3xx is an error, never followed), so the key cannot be steered to
  another host.
* **No echo.** A provider error body is read (bounded) only to pick a stable code out of
  a whitelist of ``detail.status`` values; nothing from it reaches a message or a log.

Request and response bodies are capped, one deadline covers the whole exchange (retries
included; a streamed body gets the same deadline per chunk, as an idle limit), and a 429 is
retried a bounded number of times unless the account is out of quota.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from baselith.core.observability.logging import get_logger
from baselith.core.security.bounded_body import (
    BOUNDED_ACCEPT_ENCODING,
    BodyTooLargeError,
    UnsupportedEncodingError,
    aiter_bounded,
    read_bounded,
)
from baselith.core.security.http import create_hardened_async_client
from baselith.core.security.ssrf import SsrfError, SsrfPolicy
from baselith.core.services.voice._elevenlabs_catalog import (
    VOICE_ID,
    CatalogCalls,
    parse_voices,
)
from baselith.core.services.voice._elevenlabs_errors import KNOWN_STATUS, Kind, classify
from baselith.core.services.voice._elevenlabs_payload import (
    REQUEST_ID,
    is_stitched,
    speech_payload,
    valid_output_format,
)
from baselith.core.services.voice._elevenlabs_stt import ScribeCalls
from baselith.core.services.voice._http_providers import read_voice_body
from baselith.core.services.voice.errors import (
    VoiceEgressError,
    VoiceInputError,
    VoiceLimitError,
    VoiceNotFoundError,
    VoiceRateLimitedError,
    VoiceTimeoutError,
    VoiceUnavailableError,
    VoiceUpstreamError,
    safe_text,
)
from baselith.core.services.voice.models import SpeechContext

logger = get_logger(__name__)

_ERROR_BODY_MAX = 4096
_RETRY_AFTER_MAX = 2.0


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


@dataclass(frozen=True)
class SpeechStream:
    """One passage being synthesised: the audio as it arrives, and what the provider told.

    The request is already answered (status checked, so a failure was raised before this
    exists); ``chunks`` yields the body under the response cap and an idle deadline per chunk,
    and raises a :class:`~baselith.core.services.voice.errors.VoiceError` on a mid-stream failure.
    Iterate it to the end or close it (``aclose``) so the connection returns to the pool.
    """

    chunks: AsyncIterator[bytes]
    request_id: str | None = None
    stitched: bool = False
    content_type: str = "audio/mpeg"


#: How long an idle connection to the provider is kept for the next sentence.
_KEEPALIVE_SECONDS = 45.0


class ElevenLabsClient(CatalogCalls, ScribeCalls):
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

    async def speech(self, target: ElevenLabsTarget, text: str, **kw: Any) -> bytes:
        """Synthesize ``text``; returns the audio bytes (the arguments of :meth:`speech_result`)."""
        result = await self.speech_result(target, text, **kw)
        return result.audio

    def _speech_request(
        self,
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
    ) -> tuple[str, dict[str, Any]]:
        """The path and body of one synthesis, once the inputs are checked."""
        if not text.strip():
            raise VoiceInputError("text is empty")
        if not VOICE_ID.fullmatch(voice_id):
            raise VoiceNotFoundError("the voice id is not valid")
        if not valid_output_format(output_format):
            raise VoiceInputError("the output format is not valid")
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
        return f"/v1/text-to-speech/{quote(voice_id, safe='')}", payload

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
        path, payload = self._speech_request(
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
        audio, headers = await self._exchange(
            target,
            "POST",
            path,
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

    async def speech_stream(
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
    ) -> SpeechStream:
        """Synthesize ``text`` on the ``/stream`` endpoint: the audio as it is generated.

        The provider's answer is checked before this returns (a refusal raises here, as for a
        whole-body call); the body then arrives through :attr:`SpeechStream.chunks`.
        """
        path, payload = self._speech_request(
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
        response = await self._open(
            target,
            "POST",
            path + "/stream",
            kind="tts",
            params={"output_format": output_format},
            json=payload,
        )
        raw = response.headers.get("request-id", "")
        return SpeechStream(
            chunks=self._stream_body(response),
            request_id=raw if REQUEST_ID.fullmatch(raw) else None,
            stitched=is_stitched(payload),
            content_type=response.headers.get("content-type", "").split(";")[0].strip()
            or "application/octet-stream",
        )

    async def _stream_body(self, response: httpx.Response) -> AsyncIterator[bytes]:
        """The body of ``response`` under the cap; every chunk waits at most the deadline."""
        total = 0
        try:
            chunks = aiter_bounded(response, self._max_response)
            while True:
                try:
                    async with asyncio.timeout(self._timeout):
                        chunk = await anext(chunks)
                except StopAsyncIteration:
                    break
                total += len(chunk)
                yield chunk
            if total == 0:
                raise VoiceUpstreamError("the speech response was empty")
        except BodyTooLargeError:
            raise VoiceLimitError("the response exceeds the size cap") from None
        except UnsupportedEncodingError as exc:
            raise VoiceUpstreamError(str(exc)) from None
        except (TimeoutError, httpx.TimeoutException):
            raise VoiceTimeoutError(
                "the voice provider did not answer in time"
            ) from None
        except httpx.HTTPError as exc:
            logger.warning("voice_transport_failed", error_type=type(exc).__name__)
            raise VoiceUnavailableError(
                "the voice provider could not be reached"
            ) from None
        finally:
            await response.aclose()

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
        """One call read whole: the body (under the cap) and the headers, within one deadline."""
        async with self._guarded(target):
            async with asyncio.timeout(self._timeout):
                response = await self._open_within(
                    target, method, path, kind=kind, **kwargs
                )
                try:
                    return await self._read(response), response.headers
                finally:
                    await response.aclose()

    async def _open(
        self,
        target: ElevenLabsTarget,
        method: str,
        path: str,
        *,
        kind: Kind,
        **kwargs: Any,
    ) -> httpx.Response:
        """One call answered 2xx, its body still unread (the caller streams and closes it)."""
        async with self._guarded(target):
            async with asyncio.timeout(self._timeout):
                return await self._open_within(
                    target, method, path, kind=kind, **kwargs
                )

    def _guarded(self, target: ElevenLabsTarget) -> _Guard:
        return _Guard(target)

    async def _open_within(
        self,
        target: ElevenLabsTarget,
        method: str,
        path: str,
        *,
        kind: Kind,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send (retrying a 429), refuse a redirect, classify a failure; a 2xx comes back open."""
        headers = {
            "xi-api-key": target.api_key,
            "Accept-Encoding": BOUNDED_ACCEPT_ENCODING,
        }
        url = self._base + path
        for attempt in range(self._retries + 1):
            request = self._client.build_request(method, url, headers=headers, **kwargs)
            response = await self._client.send(request, stream=True)
            status = response.status_code
            if 200 <= status < 300:
                return response
            try:
                if 300 <= status < 400:
                    logger.warning("voice_redirect_refused", status=status)
                    raise VoiceUnavailableError(
                        "the voice provider redirected the call"
                    )
                detail = await self._detail(response)
                error = classify(status, detail, kind)
                if not (
                    isinstance(error, VoiceRateLimitedError) and attempt < self._retries
                ):
                    raise error
                pause = self._pause(response, attempt)
            finally:
                await response.aclose()
            await asyncio.sleep(pause)
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


class _Guard:
    """Translate transport failures of one exchange into the stable voice errors."""

    def __init__(self, target: ElevenLabsTarget) -> None:
        self._target = target

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc is None:
            return
        if isinstance(exc, SsrfError):
            raise VoiceEgressError(
                safe_text(str(exc), self._target.secrets())
            ) from None
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            raise VoiceTimeoutError(
                "the voice provider did not answer in time"
            ) from None
        if isinstance(exc, httpx.HTTPError):
            logger.warning("voice_transport_failed", error_type=type(exc).__name__)
            raise VoiceUnavailableError(
                "the voice provider could not be reached"
            ) from None


__all__ = [
    "VOICE_ID",
    "ElevenLabsClient",
    "ElevenLabsTarget",
    "SpeechResult",
    "SpeechStream",
    "classify",
    "parse_voices",
]
