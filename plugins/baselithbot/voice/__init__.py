"""Voice + audio surface (TTS, ElevenLabs, wake, audio capture)."""

from plugins.baselithbot.voice.audio_capture import (
    AudioBackendError,
    EnergyThresholdWake,
    SoundDeviceAudioBackend,
)
from plugins.baselithbot.voice.elevenlabs import ElevenLabsTTS
from plugins.baselithbot.voice.tts import SystemTTS, TTSAdapter
from plugins.baselithbot.voice.wake import VoiceWake, WakeStatus

__all__ = [
    "TTSAdapter",
    "SystemTTS",
    "ElevenLabsTTS",
    "VoiceWake",
    "WakeStatus",
    "AudioBackendError",
    "SoundDeviceAudioBackend",
    "EnergyThresholdWake",
]
