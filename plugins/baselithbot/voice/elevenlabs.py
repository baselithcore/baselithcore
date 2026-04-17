"""ElevenLabs TTS adapter (HTTP API)."""

from __future__ import annotations

from typing import Any

from .tts import TTSAdapter


class ElevenLabsTTS(TTSAdapter):
    """Synthesize speech via the ElevenLabs HTTP API."""

    name = "elevenlabs"

    def __init__(
        self,
        api_key: str | None = None,
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",
        model_id: str = "eleven_monolingual_v1",
    ) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id

    async def synthesize(self, text: str, voice: str | None = None) -> dict[str, Any]:
        if not self._api_key:
            return {"status": "unconfigured", "missing": ["api_key"]}
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        voice_id = voice or self._voice_id
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": self._api_key,
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
        }
        payload = {"text": text, "model_id": self._model_id}

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=payload)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "audio_bytes": len(response.content) if response.is_success else 0,
        }


__all__ = ["ElevenLabsTTS"]
