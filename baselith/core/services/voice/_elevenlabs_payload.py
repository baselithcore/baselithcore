"""The JSON body of an ElevenLabs text-to-speech call, built per model capability.

Pure functions, no I/O. What a model accepts is decided here and nowhere else: an unsupported
field is dropped silently (the provider answers 400 to some of them, ignores others), so a
caller can always ask for the best continuity and let the model take what it can. The
``supports_*`` predicates are public (re-exported by :mod:`baselith.core.services.voice`), so a
host can shape its own behaviour (chunking, a speed slider, tone tags) from the model in force
without knowing the provider's model families.
"""

from __future__ import annotations

import re
from typing import Any

from baselith.core.services.voice.models import SpeechContext

#: Models that accept ``language_code``. ``eleven_multilingual_v2`` infers the language and
#: the provider documents the field as unsupported for it, so it is not sent.
LANGUAGE_MODELS = (
    "eleven_flash_v2_5",
    "eleven_turbo_v2_5",
    "eleven_v3",
    "eleven_v3_conversational",
    "eleven_v4",
    "eleven_v4_turbo",
)
#: Models without request stitching (``previous_text``, ``next_text``, ``previous_request_ids``).
NO_STITCHING_MODELS = ("eleven_v3", "eleven_v3_conversational")
#: The v4 family takes only ``stability`` and ``similarity_boost``: no ``speed``, no ``style``.
FIXED_PACE_MODELS = ("eleven_v4", "eleven_v4_turbo")
#: Models that read audio tags such as ``[warmly]`` as delivery instructions.
TAG_MODELS = ("eleven_v3", "eleven_v3_conversational", "eleven_v4", "eleven_v4_turbo")
#: What a provider request id looks like; ids come back from the provider and go back to it.
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
#: What an output format looks like (``mp3_44100_128``, ``pcm_24000``); it goes into the query.
OUTPUT_FORMAT = re.compile(r"(?:mp3|pcm|opus|wav)_\d{4,5}(?:_\d{2,3})?")
#: Most earlier request ids the provider accepts.
MAX_REQUEST_IDS = 3
#: Longest context text sent (characters nearest the passage are kept).
CONTEXT_CHARS = 600
SPEED_MIN, SPEED_MAX = 0.7, 1.2
_NORMALIZATION = ("auto", "on", "off")


def supports_stitching(model: str) -> bool:
    """Whether ``model`` takes request-stitching context."""
    return model not in NO_STITCHING_MODELS


def supports_language(model: str) -> bool:
    """Whether ``model`` takes a ``language_code``."""
    return model in LANGUAGE_MODELS


def supports_speed(model: str) -> bool:
    """Whether ``model`` honours ``voice_settings.speed`` (the v4 family does not)."""
    return model not in FIXED_PACE_MODELS


def supports_style(model: str) -> bool:
    """Whether ``model`` honours ``voice_settings.style`` (the v4 family does not)."""
    return model not in FIXED_PACE_MODELS


def supports_tags(model: str) -> bool:
    """Whether ``model`` reads bracketed audio tags as delivery instructions."""
    return model in TAG_MODELS


def valid_output_format(output_format: str) -> bool:
    """Whether ``output_format`` is a provider format name (never anything else into the query)."""
    return OUTPUT_FORMAT.fullmatch(output_format) is not None


def content_type_of(output_format: str) -> str:
    """The media type of audio in ``output_format``."""
    if output_format.startswith("pcm_"):
        return "audio/pcm"
    if output_format.startswith("opus_"):
        return "audio/opus"
    if output_format.startswith("wav_"):
        return "audio/wav"
    return "audio/mpeg"


def sample_rate_of(output_format: str) -> int | None:
    """The sample rate named by ``output_format`` (``pcm_24000`` is 24000), or None."""
    parts = output_format.split("_")
    return int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None


def context_fields(model: str, context: SpeechContext | None) -> dict[str, Any]:
    """The stitching and normalisation fields ``model`` can use out of ``context``."""
    if context is None:
        return {}
    out: dict[str, Any] = {}
    if context.normalization in _NORMALIZATION:
        out["apply_text_normalization"] = context.normalization
    if not supports_stitching(model):
        return out
    ids = [i for i in context.previous_request_ids if REQUEST_ID.fullmatch(i)]
    if ids:
        out["previous_request_ids"] = ids[-MAX_REQUEST_IDS:]
    elif context.previous_text and context.previous_text.strip():
        out["previous_text"] = context.previous_text[-CONTEXT_CHARS:]
    if context.next_text and context.next_text.strip():
        out["next_text"] = context.next_text[:CONTEXT_CHARS]
    return out


def is_stitched(payload: dict[str, Any]) -> bool:
    """Whether a payload carries any continuity context."""
    return any(
        k in payload for k in ("previous_text", "next_text", "previous_request_ids")
    )


def speech_payload(
    text: str,
    *,
    model: str,
    stability: float,
    similarity_boost: float,
    style: float,
    speed: float,
    language: str | None,
    context: SpeechContext | None,
) -> dict[str, Any]:
    """The full request body for one passage."""
    settings: dict[str, float] = {
        "stability": stability,
        "similarity_boost": similarity_boost,
    }
    if supports_style(model):
        settings["style"] = style
    if supports_speed(model) and abs(speed - 1.0) > 0.01:
        settings["speed"] = min(SPEED_MAX, max(SPEED_MIN, speed))
    payload: dict[str, Any] = {
        "text": text,
        "model_id": model,
        "voice_settings": settings,
    }
    if language and supports_language(model):
        payload["language_code"] = language
    payload.update(context_fields(model, context))
    return payload


__all__ = [
    "FIXED_PACE_MODELS",
    "LANGUAGE_MODELS",
    "NO_STITCHING_MODELS",
    "REQUEST_ID",
    "TAG_MODELS",
    "content_type_of",
    "context_fields",
    "is_stitched",
    "sample_rate_of",
    "speech_payload",
    "supports_language",
    "supports_speed",
    "supports_stitching",
    "supports_style",
    "supports_tags",
    "valid_output_format",
]
