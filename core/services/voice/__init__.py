"""
Voice Service Module.

Provides text-to-speech (TTS) and speech-to-text (STT) capabilities
using various providers (OpenAI, ElevenLabs, Google, local).

Enables voice-based interaction with agents.
"""

from core.services.voice._routing import VoiceCapabilities
from core.services.voice.errors import (
    VoiceEgressError,
    VoiceError,
    VoiceInputError,
    VoiceKeyInvalidError,
    VoiceLimitError,
    VoiceNotConfiguredError,
    VoiceNotFoundError,
    VoicePermissionMissingError,
    VoiceProviderError,
    VoiceQuotaError,
    VoiceRateLimitedError,
    VoiceTimeoutError,
    VoiceUnavailableError,
    VoiceUpstreamError,
)
from core.services.voice.models import (
    SpeechContext,
    SpeechTuning,
    STTRequest,
    TTSRequest,
    VoiceInfo,
    VoiceOverrides,
    VoiceProbe,
    VoiceProvider,
    VoiceRequest,
    VoiceResponse,
)
from core.services.voice.service import VoiceService

__all__ = [
    "STTRequest",
    "SpeechContext",
    "SpeechTuning",
    "TTSRequest",
    "VoiceCapabilities",
    "VoiceEgressError",
    "VoiceError",
    "VoiceInfo",
    "VoiceInputError",
    "VoiceKeyInvalidError",
    "VoiceLimitError",
    "VoiceNotConfiguredError",
    "VoiceNotFoundError",
    "VoiceOverrides",
    "VoicePermissionMissingError",
    "VoiceProbe",
    "VoiceProvider",
    "VoiceProviderError",
    "VoiceQuotaError",
    "VoiceRateLimitedError",
    "VoiceRequest",
    "VoiceResponse",
    "VoiceService",
    "VoiceTimeoutError",
    "VoiceUnavailableError",
    "VoiceUpstreamError",
]
