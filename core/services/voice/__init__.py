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
    VoiceLimitError,
    VoiceNotConfiguredError,
    VoiceTimeoutError,
    VoiceUpstreamError,
)
from core.services.voice.models import (
    STTRequest,
    TTSRequest,
    VoiceProvider,
    VoiceRequest,
    VoiceResponse,
)
from core.services.voice.service import VoiceService

__all__ = [
    "STTRequest",
    "TTSRequest",
    "VoiceCapabilities",
    "VoiceEgressError",
    "VoiceError",
    "VoiceInputError",
    "VoiceLimitError",
    "VoiceNotConfiguredError",
    "VoiceProvider",
    "VoiceRequest",
    "VoiceResponse",
    "VoiceService",
    "VoiceTimeoutError",
    "VoiceUpstreamError",
]
