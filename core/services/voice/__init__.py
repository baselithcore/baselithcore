"""
Voice Service Module.

Provides text-to-speech (TTS) and speech-to-text (STT) capabilities
using various providers (OpenAI, ElevenLabs, Google, local).

Enables voice-based interaction with agents.
"""

from core.services.voice.service import VoiceService
from core.services.voice.models import (
    VoiceRequest,
    VoiceResponse,
    TTSRequest,
    STTRequest,
    VoiceProvider,
)

__all__ = [
    "VoiceService",
    "VoiceRequest",
    "VoiceResponse",
    "TTSRequest",
    "STTRequest",
    "VoiceProvider",
]
