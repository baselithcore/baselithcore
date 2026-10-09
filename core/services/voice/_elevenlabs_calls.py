"""The ElevenLabs call paths of ``VoiceService`` (native provider, not OpenAI-compatible).

Kept apart from :mod:`core.services.voice.service` (size cap). The credential comes from the
environment first (``VOICE_ELEVENLABS_API_KEY``, or the key passed to the service) and only
then from the host's overrides (:class:`~core.services.voice.models.VoiceOverrides`, an admin
console's vault); the voice and model are the other way round (an override wins), as for any
plain setting. Failures are :class:`~core.services.voice.errors.VoiceError`, with the stable
codes of :mod:`core.services.voice.errors`; no message carries a provider body or the key.
"""

from __future__ import annotations

import hashlib
from typing import Any

from core.observability.logging import get_logger
from core.services.voice import _usage
from core.services.voice._elevenlabs import ElevenLabsClient, ElevenLabsTarget
from core.services.voice.errors import (
    VoiceError,
    VoiceInputError,
    VoiceLimitError,
    VoiceNotConfiguredError,
)
from core.services.voice.models import (
    SpeechContext,
    SpeechTuning,
    STTRequest,
    VoiceInfo,
    VoiceOverrides,
    VoiceProbe,
    VoiceResponse,
)

logger = get_logger(__name__)

# Upload file extension per MIME type: the provider sniffs the container from the name.
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


class ElevenLabsCalls:
    """Mixin of ``VoiceService``: TTS, STT, the voice list and the key check."""

    _elevenlabs_key: str | None
    _eleven: ElevenLabsClient | None
    _overrides_fn: Any

    def _config(self) -> Any:  # pragma: no cover - provided by VoiceService
        raise NotImplementedError

    def _overrides(self) -> VoiceOverrides | None:
        """The host's current overrides, or None (a failing provider function means none)."""
        if self._overrides_fn is None:
            return None
        try:
            found = self._overrides_fn()
        except Exception as exc:
            logger.warning("voice_overrides_failed", error_type=type(exc).__name__)
            return None
        return found if isinstance(found, VoiceOverrides) else None

    def _eleven_key(self) -> str | None:
        """The API key in force: the environment's, else the host's override."""
        if self._elevenlabs_key:
            return self._elevenlabs_key
        override = self._overrides()
        return (
            override.elevenlabs_api_key
            if override and override.elevenlabs_api_key
            else None
        )

    def elevenlabs_key_fingerprint(self) -> str | None:
        """A short digest of the key in force, for cache invalidation; never the key."""
        key = self._eleven_key()
        return hashlib.sha256(key.encode()).hexdigest()[:12] if key else None

    def _eleven_target(self) -> ElevenLabsTarget:
        key = self._eleven_key()
        if not key:
            raise VoiceNotConfiguredError("the ElevenLabs API key is not set")
        return ElevenLabsTarget(key)

    def _eleven_client(self) -> ElevenLabsClient:
        if self._eleven is None:
            cfg = self._config()
            self._eleven = ElevenLabsClient(
                base_url=str(cfg.elevenlabs_base_url),
                allow_internal=bool(cfg.allow_internal_endpoints),
                timeout_seconds=float(cfg.timeout_seconds),
                max_request_bytes=int(cfg.max_audio_bytes),
                max_response_bytes=int(cfg.max_response_bytes),
                retries=int(cfg.elevenlabs_retries),
            )
        return self._eleven

    def _eleven_default_voice(self) -> str:
        override = self._overrides()
        if override and override.elevenlabs_voice_id:
            return override.elevenlabs_voice_id
        return str(self._config().elevenlabs_voice_id)

    def _eleven_tts_model(self) -> str:
        override = self._overrides()
        if override and override.elevenlabs_tts_model:
            return override.elevenlabs_tts_model
        return str(self._config().elevenlabs_tts_model)

    def elevenlabs_explicit(self) -> frozenset[str]:
        """Which synthesis settings the operator chose (``model``, ``stability``,
        ``similarity_boost``, ``style``): set in the environment, or by a host override.

        A per-call :class:`SpeechTuning` fills only the others. Never raises.
        """
        found: set[str] = set()
        fields: set[str] = set(getattr(self._config(), "model_fields_set", ()))
        for name, key in (
            ("elevenlabs_tts_model", "model"),
            ("elevenlabs_stability", "stability"),
            ("elevenlabs_similarity_boost", "similarity_boost"),
            ("elevenlabs_style", "style"),
        ):
            if name in fields:
                found.add(key)
        override = self._overrides()
        if override and override.elevenlabs_tts_model:
            found.add("model")
        return frozenset(found)

    def _eleven_tuned(self, tuning: SpeechTuning | None) -> dict[str, Any]:
        """Model and voice settings in force: operator's explicit, else the tuning, else default."""
        cfg = self._config()
        explicit = self.elevenlabs_explicit()
        chosen = tuning or SpeechTuning()
        out: dict[str, Any] = {}
        for key, default in (
            ("stability", cfg.elevenlabs_stability),
            ("similarity_boost", cfg.elevenlabs_similarity_boost),
            ("style", cfg.elevenlabs_style),
        ):
            wanted = getattr(chosen, key)
            out[key] = float(
                wanted if wanted is not None and key not in explicit else default
            )
        if chosen.model and "model" not in explicit:
            out["model"] = chosen.model
        else:
            out["model"] = self._eleven_tts_model()
        return out

    async def _tts_elevenlabs_native(
        self,
        text: str,
        voice: str | None,
        speed: float,
        *,
        language: str | None = None,
        context: SpeechContext | None = None,
        tuning: SpeechTuning | None = None,
    ) -> VoiceResponse:
        """Synthesize with ElevenLabs; the audio is MP3 (``VOICE_ELEVENLABS_OUTPUT_FORMAT``)."""
        target = self._eleven_target()
        cfg = self._config()
        if not text.strip():
            raise VoiceInputError("text is empty")
        if len(text) > cfg.max_tts_chars:
            raise VoiceLimitError("text exceeds the synthesis cap")
        voice_id = voice or self._eleven_default_voice()
        tuned = self._eleven_tuned(tuning)
        model = tuned["model"]
        logger.info(
            "tts_start",
            provider="elevenlabs",
            text_length=len(text),
            model=model,
            stitched=context is not None,
        )
        result = await self._eleven_client().speech_result(
            target,
            text,
            voice_id=voice_id,
            model=model,
            output_format=str(cfg.elevenlabs_output_format),
            stability=tuned["stability"],
            similarity_boost=tuned["similarity_boost"],
            style=tuned["style"],
            speed=speed,
            language=language,
            context=context,
        )
        # Billed characters: a stitched request still counts, its context text does not.
        _usage.report_tts_characters(model, len(text))
        return VoiceResponse(
            success=True,
            content=result.audio,
            provider="elevenlabs",
            model=model,
            metadata={
                "voice": voice_id,
                "format": "mp3",
                "request_id": result.request_id,
                "stitched": result.stitched,
            },
        )

    async def _stt_elevenlabs(
        self,
        request: STTRequest,
        content_type: str | None,
        duration_seconds: float | None,
    ) -> VoiceResponse:
        """Transcribe with ElevenLabs Scribe; the language is passed only when known."""
        target = self._eleven_target()
        cfg = self._config()
        model = str(cfg.elevenlabs_stt_model)
        audio = request.get_audio_bytes()
        mime = (content_type or "application/octet-stream").split(";")[0].strip()
        text, measured = await self._eleven_client().transcribe(
            target,
            audio,
            model=model,
            filename=f"audio.{_EXT.get(mime, 'bin')}",
            content_type=mime,
            language=request.language,
        )
        seconds = measured or duration_seconds
        if seconds is None or seconds <= 0:
            seconds = len(audio) / _usage.FALLBACK_BYTES_PER_SECOND
        _usage.report_stt_seconds(model, seconds)
        return VoiceResponse(
            success=True,
            content=text,
            provider="elevenlabs",
            model=model,
            duration_seconds=float(seconds),
            language=request.language,
        )

    async def list_voices(self) -> list[VoiceInfo]:
        """The voices the ElevenLabs account can use. Raises :class:`VoiceError`."""
        return await self._eleven_client().voices(self._eleven_target())

    async def probe_elevenlabs(self) -> VoiceProbe:
        """Check the key with one cheap read; never raises, never echoes the key.

        ``ok`` only when the key works and, where the key may read it, the account still has
        characters left. The subscription read needs the ``user_read`` permission, which the
        recommended restricted key does not carry, so its absence is not a failure.
        """
        try:
            target = self._eleven_target()
            client = self._eleven_client()
            await client.check(target)
        except VoiceError as exc:
            return VoiceProbe(ok=False, code=exc.code)
        spent = await client.usage(target)
        if spent is None:
            return VoiceProbe(ok=True, code="ok")
        used, limit = spent
        if used >= limit:
            return VoiceProbe(False, "quota_exceeded", used, limit)
        return VoiceProbe(True, "ok", used, limit)


__all__ = ["ElevenLabsCalls"]
