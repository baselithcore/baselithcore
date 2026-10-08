"""The plain-HTTP provider paths of ``VoiceService`` (Google Cloud)."""

from __future__ import annotations

from typing import Any

from core.services.voice.models import STTRequest, TTSRequest, VoiceResponse


class HttpProviders:
    """Mixin of ``VoiceService``: TTS/STT over the shared ``httpx`` client."""

    def _config(self) -> Any:  # pragma: no cover - provided by VoiceService
        raise NotImplementedError

    def _get_http_client(self) -> Any:  # pragma: no cover - provided by VoiceService
        raise NotImplementedError

    async def _tts_google(self, request: TTSRequest) -> VoiceResponse:
        """
        Generate speech using Google Cloud Text-to-Speech.

        Args:
            request: Audio generation parameters.

        Returns:
            VoiceResponse: Generated audio data.
        """
        # Google Cloud TTS API
        _google_secret = self._config().google_api_key
        if not _google_secret:
            raise ValueError("Google API key not configured")
        google_api_key = _google_secret.get_secret_value()

        # Parse voice name for language code
        language_code = "en-US"
        if "-" in request.voice:
            parts = request.voice.split("-")
            if len(parts) >= 2:
                language_code = f"{parts[0]}-{parts[1]}"

        client = self._get_http_client()
        response = await client.post(
            "https://texttospeech.googleapis.com/v1/text:synthesize",
            params={"key": google_api_key},
            json={
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
        response.raise_for_status()
        data = response.json()

        import base64

        audio_bytes = base64.b64decode(data["audioContent"])

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
        import base64

        _google_secret = self._config().google_api_key
        if not _google_secret:
            raise ValueError("Google API key not configured")
        google_api_key = _google_secret.get_secret_value()

        audio_bytes = request.get_audio_bytes()
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        client = self._get_http_client()

        response = await client.post(
            "https://speech.googleapis.com/v1/speech:recognize",
            params={"key": google_api_key},
            json={
                "config": {
                    "encoding": "MP3",
                    "languageCode": request.language or "en-US",
                    "enableAutomaticPunctuation": True,
                },
                "audio": {"content": audio_b64},
            },
        )
        response.raise_for_status()
        data = response.json()

        # Extract transcript
        results = data.get("results", [])
        transcript = " ".join(
            r["alternatives"][0]["transcript"] for r in results if r.get("alternatives")
        )

        return VoiceResponse(
            success=True,
            content=transcript,
            provider="google",
            model="speech-v1",
            language=request.language,
        )


__all__ = ["HttpProviders"]
