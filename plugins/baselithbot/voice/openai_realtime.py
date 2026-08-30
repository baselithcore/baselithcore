"""OpenAI Realtime WebSocket adapter for the core duplex voice protocol.

Implements :class:`core.realtime.DuplexVoiceSession` over the OpenAI Realtime
API using ``aiohttp``. The WebSocket factory is an injectable seam
(``ws_connect``) so unit tests drive the adapter with scripted fakes — no
real network is ever touched in tests.

Opt-in wiring: baselithbot has no central voice-service constructor (the
sequential wake -> STT -> LLM -> TTS surface ships as composable pieces), so
the realtime vertical ships standalone behind :func:`build_realtime_loop`.
It returns ``None`` unless ``BASELITHBOT_VOICE_REALTIME_ENABLED=true``; the
API key is read from the core :class:`~core.config.multimodal.VoiceConfig`
(``VOICE_OPENAI_API_KEY`` / ``OPENAI_API_KEY``) as a ``SecretStr`` — never
stored as a plain ``str``. See ``realtime_loop`` for a composition example.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import aiohttp
from core.config.multimodal import get_voice_config
from core.observability.logging import get_logger
from core.realtime import (
    AudioDelta,
    DuplexEvent,
    ResponseDone,
    ResponseStarted,
    SessionError,
    SpeechStarted,
    SpeechStopped,
    TranscriptDelta,
)
from plugins.baselithbot.voice.realtime_loop import AudioPlayer, RealtimeVoiceLoop
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = get_logger(__name__)

_DEFAULT_URL = "wss://api.openai.com/v1/realtime"

WsConnect = Callable[[str, dict[str, str]], Awaitable[Any]]
"""Seam: ``(url, headers) -> websocket`` returning an aiohttp-compatible WS."""


class RealtimeVoiceSettings(BaseSettings):
    """Opt-in configuration for the realtime duplex voice vertical."""

    model_config = SettingsConfigDict(
        env_prefix="BASELITHBOT_VOICE_",
        case_sensitive=False,
        extra="ignore",
    )

    realtime_enabled: bool = Field(
        default=False,
        description="Select the realtime duplex loop over the sequential "
        "wake/STT/LLM/TTS pipeline.",
    )
    realtime_model: str = Field(
        default="gpt-4o-realtime-preview",
        description="OpenAI Realtime model identifier.",
    )
    realtime_voice: str = Field(default="alloy", description="Assistant voice preset.")
    realtime_silence_duration_ms: int = Field(
        default=500,
        ge=100,
        le=5000,
        description="Server-VAD silence window marking end of user speech.",
    )
    realtime_latency_budget_ms: float = Field(
        default=500.0,
        gt=0.0,
        description="Warn when speech-stop to first assistant audio exceeds this budget.",
    )


def _map_server_event(payload: dict[str, Any]) -> DuplexEvent | None:
    """Translate one OpenAI Realtime server event into a duplex event."""
    match payload.get("type"):
        case "input_audio_buffer.speech_started":
            return SpeechStarted()
        case "input_audio_buffer.speech_stopped":
            return SpeechStopped()
        case "response.created":
            return ResponseStarted()
        case "response.audio.delta":
            return AudioDelta(data=base64.b64decode(payload.get("delta", "")))
        case "response.audio_transcript.delta":
            return TranscriptDelta(text=str(payload.get("delta", "")), role="assistant")
        case "conversation.item.input_audio_transcription.completed":
            return TranscriptDelta(text=str(payload.get("transcript", "")), role="user")
        case "response.done":
            return ResponseDone()
        case "error":
            error = payload.get("error") or {}
            message = str(error.get("message") or payload)
            return SessionError(message=message)
        case _:
            return None


class OpenAIRealtimeSession:
    """:class:`core.realtime.DuplexVoiceSession` over the OpenAI Realtime WS.

    Connects lazily on first use, immediately sending ``session.update`` with
    server-VAD turn detection. Connection failures surface as a
    :class:`SessionError` event followed by the end of the stream.
    """

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        model: str = "gpt-4o-realtime-preview",
        voice: str = "alloy",
        silence_duration_ms: int = 500,
        url: str = _DEFAULT_URL,
        ws_connect: WsConnect | None = None,
    ) -> None:
        """Initialize the session.

        Args:
            api_key: OpenAI API key (``SecretStr``); ``None`` fails the
                session with a ``SessionError`` on first use.
            model: Realtime model identifier.
            voice: Assistant voice preset.
            silence_duration_ms: Server-VAD silence window (ms).
            url: WebSocket endpoint (query ``model`` is appended).
            ws_connect: Injectable ``(url, headers) -> ws`` factory; defaults
                to a real ``aiohttp`` connection.
        """
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._silence_duration_ms = silence_duration_ms
        self._url = url
        self._ws_connect = ws_connect
        self._ws: Any | None = None
        self._http: aiohttp.ClientSession | None = None
        self._closed = False
        self._connect_lock = asyncio.Lock()

    @property
    def closed(self) -> bool:
        """Whether the session is closed or terminally failed."""
        return self._closed

    async def _default_ws_connect(self, url: str, headers: dict[str, str]) -> Any:
        self._http = aiohttp.ClientSession()
        return await self._http.ws_connect(url, headers=headers, heartbeat=20.0)

    async def _ensure_connected(self) -> None:
        async with self._connect_lock:
            if self._closed:
                raise RuntimeError("realtime session is closed")
            if self._ws is not None:
                return
            if self._api_key is None:
                raise RuntimeError(
                    "OpenAI API key not configured (set VOICE_OPENAI_API_KEY or OPENAI_API_KEY)"
                )
            url = f"{self._url}?model={self._model}"
            headers = {
                "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                "OpenAI-Beta": "realtime=v1",
            }
            connect = self._ws_connect or self._default_ws_connect
            self._ws = await connect(url, headers)
            await self._ws.send_json(
                {
                    "type": "session.update",
                    "session": {
                        "turn_detection": {
                            "type": "server_vad",
                            "silence_duration_ms": self._silence_duration_ms,
                        },
                        "voice": self._voice,
                        "modalities": ["audio", "text"],
                    },
                }
            )
            logger.info(
                "baselithbot_realtime_connected",
                model=self._model,
                silence_duration_ms=self._silence_duration_ms,
            )

    async def events(self) -> AsyncIterator[DuplexEvent]:
        """Connect (if needed) and return the inbound event stream."""
        try:
            await self._ensure_connected()
        except (aiohttp.ClientError, OSError, RuntimeError) as exc:
            self._closed = True
            logger.error("baselithbot_realtime_connect_failed", error=str(exc))
            return self._error_stream(str(exc))
        return self._event_stream()

    async def _error_stream(self, message: str) -> AsyncIterator[DuplexEvent]:
        yield SessionError(message=message)

    async def _event_stream(self) -> AsyncIterator[DuplexEvent]:
        ws = self._ws
        if ws is None:  # pragma: no cover - guarded by _ensure_connected
            return
        try:
            async for msg in ws:
                if msg.type is aiohttp.WSMsgType.TEXT:
                    event = _map_server_event(json.loads(msg.data))
                    if event is not None:
                        yield event
                elif msg.type is aiohttp.WSMsgType.ERROR:
                    self._closed = True
                    yield SessionError(message=f"websocket error: {msg.data}")
                    return
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    self._closed = True
                    return
        except aiohttp.ClientError as exc:
            self._closed = True
            yield SessionError(message=str(exc))

    async def send_audio(self, pcm: bytes) -> None:
        """Append base64 PCM16 audio to the provider's input buffer."""
        await self._ensure_connected()
        if self._ws is None:  # pragma: no cover - guarded by _ensure_connected
            raise RuntimeError("realtime session has no transport")
        await self._ws.send_json(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }
        )

    async def cancel_response(self) -> None:
        """Abort the in-flight assistant response (barge-in)."""
        if self._closed:
            logger.debug("baselithbot_realtime_cancel_after_close")
            return
        await self._ensure_connected()
        if self._ws is not None:
            await self._ws.send_json({"type": "response.cancel"})

    async def close(self) -> None:
        """Close the WebSocket and any owned HTTP session."""
        self._closed = True
        ws, self._ws = self._ws, None
        if ws is not None:
            await ws.close()
        http, self._http = self._http, None
        if http is not None:
            await http.close()


def build_realtime_loop(
    player: AudioPlayer,
    *,
    settings: RealtimeVoiceSettings | None = None,
    api_key: SecretStr | None = None,
    ws_connect: WsConnect | None = None,
    on_transcript: Callable[[TranscriptDelta], None] | None = None,
) -> RealtimeVoiceLoop | None:
    """Build the opt-in realtime voice loop, or ``None`` when disabled.

    Args:
        player: Playback target for assistant audio.
        settings: Realtime settings; read from the environment when omitted.
        api_key: Override key; defaults to the core voice config
            (``VOICE_OPENAI_API_KEY`` / ``OPENAI_API_KEY``, ``SecretStr``).
        ws_connect: Injectable WebSocket factory (tests).
        on_transcript: Optional transcript callback for the loop.

    Returns:
        A ready :class:`RealtimeVoiceLoop`, or ``None`` when
        ``realtime_enabled`` is off (the sequential pipeline stays in charge).
    """
    settings = settings or RealtimeVoiceSettings()
    if not settings.realtime_enabled:
        return None
    key = api_key if api_key is not None else get_voice_config().openai_api_key
    session = OpenAIRealtimeSession(
        api_key=key,
        model=settings.realtime_model,
        voice=settings.realtime_voice,
        silence_duration_ms=settings.realtime_silence_duration_ms,
        ws_connect=ws_connect,
    )
    return RealtimeVoiceLoop(
        session,
        player,
        on_transcript=on_transcript,
        latency_budget_ms=settings.realtime_latency_budget_ms,
    )


__all__ = [
    "OpenAIRealtimeSession",
    "RealtimeVoiceSettings",
    "WsConnect",
    "build_realtime_loop",
]
