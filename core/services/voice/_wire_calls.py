"""The OpenAI-wire call paths of ``VoiceService`` (governed pin or compatible sidecar)."""

from __future__ import annotations

from core.observability.logging import get_logger
from core.services.voice import _usage
from core.services.voice._openai_wire import WireTarget
from core.services.voice._routing import WireRouting
from core.services.voice.errors import VoiceInputError, VoiceLimitError
from core.services.voice.models import AudioFormat, STTRequest, VoiceResponse

logger = get_logger(__name__)

# Upload file extension per MIME type: servers sniff the container from the name.
_EXT = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/aac": "aac",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/flac": "flac",
}


class WireCalls(WireRouting):
    """Mixin of ``VoiceService``: STT and TTS over :class:`OpenAIWireClient`."""

    async def _tts_wire(
        self,
        route: tuple[WireTarget, str],
        text: str,
        voice: str | None,
        speed: float,
        format: AudioFormat,
    ) -> VoiceResponse:
        """Synthesize over the OpenAI-wire client (governed pin or sidecar)."""
        target, model = route
        cfg = self._config()
        if not text.strip():
            raise VoiceInputError("text is empty")
        if len(text) > cfg.max_tts_chars:
            raise VoiceLimitError("text exceeds the synthesis cap")
        voice = voice or cfg.tts_voice
        fmt = "wav" if format == AudioFormat.WAV else "mp3"
        logger.info(
            "tts_start", provider="openai_wire", voice=voice, text_length=len(text)
        )
        audio = await self._wire_client().speech(
            target, text, model=model, voice=voice, fmt=fmt, speed=speed
        )
        _usage.report_tts(model, text)
        return VoiceResponse(
            success=True,
            content=audio,
            provider="openai_compatible",
            model=model,
            metadata={"voice": voice, "format": fmt},
        )

    async def _stt_wire(
        self,
        route: tuple[WireTarget, str],
        request: STTRequest,
        content_type: str | None,
        duration_seconds: float | None,
    ) -> VoiceResponse:
        """Transcribe over the OpenAI-wire client (governed pin or sidecar)."""
        target, model = route
        audio = request.get_audio_bytes()
        mime = (content_type or "application/octet-stream").split(";")[0].strip()
        ext = _EXT.get(mime, "bin")
        text = await self._wire_client().transcribe(
            target,
            audio,
            model=model,
            filename=f"audio.{ext}",
            content_type=mime,
            language=request.language,
        )
        _usage.report_stt(model, len(audio), text, duration_seconds)
        return VoiceResponse(
            success=True,
            content=text,
            provider="openai_compatible",
            model=model,
            language=request.language,
        )


__all__ = ["WireCalls"]
