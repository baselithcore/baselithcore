"""Voice + audio surface (TTS, ElevenLabs, wake, audio capture, realtime)."""

from plugins.baselithbot.voice.audio_capture import (
    AudioBackendError,
    EnergyThresholdWake,
    SoundDeviceAudioBackend,
)
from plugins.baselithbot.voice.elevenlabs import ElevenLabsTTS
from plugins.baselithbot.voice.openai_realtime import (
    OpenAIRealtimeSession,
    RealtimeVoiceSettings,
    build_realtime_loop,
)
from plugins.baselithbot.voice.realtime_loop import (
    AudioPlayer,
    BufferedAudioPlayer,
    RealtimeVoiceLoop,
)
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
    "AudioPlayer",
    "BufferedAudioPlayer",
    "RealtimeVoiceLoop",
    "OpenAIRealtimeSession",
    "RealtimeVoiceSettings",
    "build_realtime_loop",
]
