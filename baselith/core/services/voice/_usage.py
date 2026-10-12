"""Usage reporting for voice calls, through the existing LLM usage seam.

Audio is not metered in tokens, but the seam every consumer already reads
(:func:`baselith.core.services.llm._telemetry.report_external_usage`: per-plugin cost
attribution, per-user accounting, the usage sinks) only knows tokens. Rather than
add a usage kind, each call is reported as a documented **token-equivalent
estimate** under its own model label, so it is attributed and visible but never
priced (unknown model ids have no price):

* STT, label ``stt:<model>``: prompt = ``ceil(seconds * 50)`` (the audio-token rate
  of speech models; ``seconds`` is the caller's ``duration_seconds`` or, without
  it, ``bytes / 12000``, roughly 96 kbit/s), completion = ``ceil(chars / 4)``.
* TTS, label ``tts:<model>``: prompt = ``ceil(chars / 4)``, completion = 0.

* ElevenLabs bills its own units, which the token-shaped seam cannot name, so they ride in
  the prompt field with the unit in the label: ``tts:elevenlabs/<model>`` reports
  **characters** (``prompt`` = chars), ``stt:elevenlabs/<model>`` reports **audio seconds**
  (``prompt`` = ceil(seconds)). Both stay unpriced (unknown model ids have no price) and
  attributed to whichever plugin/scope is bound to the call (Lith: scope ``voice``). Never the
  text or the audio, only the counts.

Reporting never raises: the audio was already produced.
"""

from __future__ import annotations

import math

from baselith.core.observability.logging import get_logger

logger = get_logger(__name__)

AUDIO_TOKENS_PER_SECOND = 50
CHARS_PER_TOKEN = 4
FALLBACK_BYTES_PER_SECOND = 12000


def report_stt(
    model: str, audio_bytes: int, text: str, duration_seconds: float | None = None
) -> None:
    """Report one transcription as a token-equivalent estimate."""
    seconds = (
        duration_seconds
        if duration_seconds is not None and duration_seconds > 0
        else audio_bytes / FALLBACK_BYTES_PER_SECOND
    )
    _report(
        f"stt:{model}",
        math.ceil(seconds * AUDIO_TOKENS_PER_SECOND),
        math.ceil(len(text) / CHARS_PER_TOKEN),
    )


def report_tts(model: str, text: str) -> None:
    """Report one synthesis as a token-equivalent estimate."""
    _report(f"tts:{model}", math.ceil(len(text) / CHARS_PER_TOKEN), 0)


def report_tts_characters(model: str, characters: int) -> None:
    """Report one ElevenLabs synthesis in the unit it bills: characters."""
    _report(f"tts:elevenlabs/{model}", max(int(characters), 0), 0)


def report_stt_seconds(model: str, seconds: float) -> None:
    """Report one ElevenLabs transcription in the unit it bills: audio seconds."""
    _report(f"stt:elevenlabs/{model}", math.ceil(max(seconds, 0.0)), 0)


def _report(label: str, prompt: int, completion: int) -> None:
    try:
        from baselith.core.services.llm._telemetry import report_external_usage

        report_external_usage(label, prompt_tokens=prompt, completion_tokens=completion)
    except Exception as exc:
        logger.debug("voice_usage_report_failed", error_type=type(exc).__name__)


__all__ = ["report_stt", "report_stt_seconds", "report_tts", "report_tts_characters"]
