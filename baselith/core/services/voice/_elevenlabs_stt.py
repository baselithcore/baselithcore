"""The speech-to-text call of the ElevenLabs client (the Scribe models).

A mixin of :class:`baselith.core.services.voice._elevenlabs.ElevenLabsClient`, which provides the
wire (``_call``, ``_json``) and the upload cap. Kept apart from the speech calls for size.
"""

from __future__ import annotations

from typing import Any, Protocol

from baselith.core.services.voice._elevenlabs_errors import Kind
from baselith.core.services.voice.errors import (
    VoiceInputError,
    VoiceLimitError,
    VoiceUpstreamError,
)


class _Wire(Protocol):
    _max_request: int

    async def _call(
        self, target: Any, method: str, path: str, *, kind: Kind, **kwargs: Any
    ) -> bytes: ...

    @staticmethod
    def _json(body: bytes) -> Any: ...


class ScribeCalls:
    """Mixin: ``POST /v1/speech-to-text``."""

    async def transcribe(
        self: _Wire,
        target: Any,
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


__all__ = ["ScribeCalls"]
