"""The JSON body of an ElevenLabs text-to-speech call, built per model capability.

Pure functions, no I/O. What a model accepts is decided here and nowhere else: an unsupported
field is dropped silently (the provider answers 400 to some of them), so a caller can always ask
for the best continuity and let the model take what it can.
"""

from __future__ import annotations

import re
from typing import Any

from baselith.core.services.voice.models import SpeechContext

#: Models that accept ``language_code`` (others ignore it or answer 400 when it is sent).
LANGUAGE_MODELS = ("eleven_flash_v2_5", "eleven_turbo_v2_5", "eleven_v3")
#: Models without request stitching (``previous_text``, ``next_text``, ``previous_request_ids``).
NO_STITCHING_MODELS = ("eleven_v3",)
#: What a provider request id looks like; ids come back from the provider and go back to it.
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
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
        "style": style,
    }
    if abs(speed - 1.0) > 0.01:
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
    "LANGUAGE_MODELS",
    "NO_STITCHING_MODELS",
    "REQUEST_ID",
    "context_fields",
    "is_stitched",
    "speech_payload",
    "supports_language",
    "supports_stitching",
]
