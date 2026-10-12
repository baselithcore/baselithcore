"""Which backend serves a voice call: governed pin, compatible sidecar or the SDKs.

Kept apart from :mod:`baselith.core.services.voice.service` (size cap). Order, per call:

1. **Governed.** When the service was built for a ``plugin`` and central LLM policy
   pins that plugin/scope (:func:`baselith.core.services.llm.governed.resolve_governed_client_config`)
   to an OpenAI-wire provider (``openai`` or ``vllm``), the pin's endpoint and
   credential serve the call, whatever ``VOICE_*`` says. The pinned *chat model* is
   not applied: audio models are ``VOICE_STT_MODEL`` / ``VOICE_TTS_MODEL``. A pin to a
   provider with no OpenAI audio API is ignored with a warning. Re-resolved on every
   call, so a console change applies without a restart.
2. **Compatible sidecar.** ``VOICE_PROVIDER=openai_compatible`` (or the default
   ``openai`` provider with no OpenAI key but a ``VOICE_*_BASE_URL``) talks to
   ``VOICE_STT_BASE_URL`` / ``VOICE_TTS_BASE_URL`` with no external key.
3. **ElevenLabs.** ``VOICE_PROVIDER=elevenlabs`` (or a host override) is handled by
   :mod:`baselith.core.services.voice._elevenlabs_calls`, before any of the above applies: a governed
   pin to an OpenAI-wire provider is not consulted, because the operator named ElevenLabs.
4. Otherwise the provider SDK paths of ``VoiceService``, unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from baselith.core.observability.logging import get_logger
from baselith.core.services.voice._openai_wire import OpenAIWireClient, WireTarget
from baselith.core.services.voice.errors import VoiceNotConfiguredError
from baselith.core.services.voice.models import VoiceProvider

logger = get_logger(__name__)

OPENAI_API_ROOT = "https://api.openai.com/v1"
Kind = Literal["stt", "tts"]


@dataclass(frozen=True)
class VoiceCapabilities:
    """What the current configuration can do (no network probe).

    Attributes:
        stt: Speech-to-text is configured.
        tts: Text-to-speech is configured.
        stt_provider: Who serves STT (``openai``, ``vllm``, ``openai_compatible``,
            ``google``) or None.
        tts_provider: Who serves TTS or None. ``elevenlabs`` serves both directions.
    """

    stt: bool
    tts: bool
    stt_provider: str | None
    tts_provider: str | None


class WireRouting:
    """Mixin of ``VoiceService``: resolves the wire target of a call."""

    _plugin: str | None
    _scope: str | None
    _openai_key: str | None
    _elevenlabs_key: str | None
    _wire: OpenAIWireClient | None
    _eleven_key: Callable[[], str | None]  # ElevenLabsCalls, in the final class

    @property
    def default_provider(self) -> VoiceProvider:  # pragma: no cover - VoiceService
        raise NotImplementedError

    def _config(self) -> Any:  # pragma: no cover - overridden by VoiceService
        raise NotImplementedError

    def _governed(self) -> Any | None:
        """The governed OpenAI-wire config for this plugin, or None."""
        if not self._plugin:
            return None
        from baselith.core.services.llm.governed import resolve_governed_client_config

        governed = resolve_governed_client_config(self._plugin, self._scope)
        if governed is None:
            return None
        if not governed.speaks_openai:
            logger.warning(
                "voice_policy_pin_ignored",
                plugin=self._plugin,
                provider=governed.provider,
                reason="no OpenAI-compatible audio API",
            )
            return None
        return governed

    def _route(
        self, kind: Kind, provider: VoiceProvider
    ) -> tuple[WireTarget, str] | None:
        """``(target, model)`` when the call goes over the wire client, else None."""
        if provider not in (VoiceProvider.OPENAI, VoiceProvider.OPENAI_COMPATIBLE):
            return None
        cfg = self._config()
        model = cfg.stt_model if kind == "stt" else cfg.tts_model
        governed = self._governed()
        if governed is not None:
            return WireTarget(
                governed.api_base or OPENAI_API_ROOT, governed.openai_key()
            ), model
        url = cfg.stt_base_url if kind == "stt" else cfg.tts_base_url
        compat = provider == VoiceProvider.OPENAI_COMPATIBLE or (
            not self._openai_key and isinstance(url, str) and bool(url)
        )
        if not compat:
            return None
        if not isinstance(url, str) or not url:
            raise VoiceNotConfiguredError(
                f"VOICE_{kind.upper()}_BASE_URL is not set for the openai_compatible provider"
            )
        key = cfg.compat_api_key.get_secret_value() if cfg.compat_api_key else None
        return WireTarget(url, key), model

    def _wire_client(self) -> OpenAIWireClient:
        if self._wire is None:
            cfg = self._config()
            self._wire = OpenAIWireClient(
                allow_internal=bool(cfg.allow_internal_endpoints),
                timeout_seconds=float(cfg.timeout_seconds),
                max_request_bytes=int(cfg.max_audio_bytes),
                max_response_bytes=int(cfg.max_response_bytes),
            )
        return self._wire

    def capabilities(self) -> VoiceCapabilities:
        """What is configured right now; cheap, no network."""
        cfg = self._config()
        governed = self._governed()
        provider = self.default_provider
        stt = tts = None
        if governed is not None and provider in (
            VoiceProvider.OPENAI,
            VoiceProvider.OPENAI_COMPATIBLE,
        ):
            stt = tts = governed.provider
        else:
            compat = provider == VoiceProvider.OPENAI_COMPATIBLE or not self._openai_key
            if provider in (VoiceProvider.OPENAI, VoiceProvider.OPENAI_COMPATIBLE):
                if compat and cfg.stt_base_url:
                    stt = "openai_compatible"
                if compat and cfg.tts_base_url:
                    tts = "openai_compatible"
                if self._openai_key and provider == VoiceProvider.OPENAI:
                    stt = tts = "openai"
            if provider == VoiceProvider.GOOGLE and cfg.google_api_key:
                stt = tts = "google"
            if provider == VoiceProvider.ELEVENLABS and self._eleven_key():
                stt = tts = "elevenlabs"
        return VoiceCapabilities(stt is not None, tts is not None, stt, tts)


__all__ = ["VoiceCapabilities", "WireRouting"]
