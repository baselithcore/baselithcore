"""
Voice Service Module.

Provides text-to-speech (TTS) and speech-to-text (STT) capabilities
using various providers (OpenAI, ElevenLabs, Google, local).

Enables voice-based interaction with agents.
"""

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
    "VoiceProvider",
    "VoiceRequest",
    "VoiceResponse",
    "VoiceService",
]
