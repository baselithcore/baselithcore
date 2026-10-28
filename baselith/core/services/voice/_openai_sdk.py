"""The OpenAI SDK paths of ``VoiceService`` (``VOICE_PROVIDER=openai``, ``tts-1`` / ``whisper-1``).

Kept apart from :mod:`baselith.core.services.voice.service` for size. The SDK client is built
once, with an explicit timeout, and shared by both directions.
"""

from __future__ import annotations

from typing import Any

from baselith.core.services.voice.models import STTRequest, TTSRequest, VoiceResponse


class OpenAISdkCalls:
    """Mixin of ``VoiceService``: the OpenAI SDK text-to-speech and Whisper calls."""

    _openai_key: str | None
    _openai_client: Any

    def _get_openai_client(self) -> Any:
        """Return the lazily created shared ``AsyncOpenAI`` client.

        Built once with an explicit timeout (the SDK default is 600s) and
        reused across TTS/STT calls so each request does not open a new
        httpx pool.
        """
        if self._openai_client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError(
                    "openai package required: pip install openai"
                ) from None
            self._openai_client = AsyncOpenAI(
                api_key=self._openai_key,
                timeout=60.0,
                max_retries=2,
            )
        return self._openai_client

    async def _tts_openai(self, request: TTSRequest) -> VoiceResponse:
        """
        Generate speech using OpenAI TTS.

        Args:
            request: Audio generation parameters.

        Returns:
            VoiceResponse: Generated audio data and metadata.
        """
        if not self._openai_key:
            raise ValueError("OpenAI API key not configured")

        client = self._get_openai_client()

        response = await client.audio.speech.create(
            model=request.model,
            voice=request.voice,
            input=request.text,
            speed=request.speed,
            response_format=request.format.value,  # type: ignore[arg-type]
        )

        audio_bytes = response.content

        return VoiceResponse(
            success=True,
            content=audio_bytes,
            provider="openai",
            model=request.model,
            metadata={"voice": request.voice, "format": request.format.value},
        )

    async def _stt_openai(self, request: STTRequest) -> VoiceResponse:
        """
        Transcribe audio using OpenAI Whisper model.

        Args:
            request: Transcription parameters.

        Returns:
            VoiceResponse: Transcribed text.
        """
        if not self._openai_key:
            raise ValueError("OpenAI API key not configured")

        client = self._get_openai_client()

        # Get audio data
        audio_bytes = request.get_audio_bytes()

        # Whisper needs a file-like object with a name
        import io

        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "audio.mp3"

        transcript = await client.audio.transcriptions.create(
            model=request.model,
            file=audio_file,
            language=request.language,  # type: ignore[arg-type]
        )

        return VoiceResponse(
            success=True,
            content=transcript.text,
            provider="openai",
            model=request.model,
            language=request.language,
        )


__all__ = ["OpenAISdkCalls"]
