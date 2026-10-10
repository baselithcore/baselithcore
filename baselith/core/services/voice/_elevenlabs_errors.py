"""Mapping of an ElevenLabs HTTP failure to the stable voice error codes.

Only a whitelisted ``detail.status`` is read out of an error body; nothing else from it is
kept, so no provider text reaches a message or a log.
"""

from __future__ import annotations

from typing import Literal

from baselith.core.services.voice.errors import (
    VoiceError,
    VoiceInputError,
    VoiceKeyInvalidError,
    VoiceLimitError,
    VoiceNotFoundError,
    VoicePermissionMissingError,
    VoiceQuotaError,
    VoiceRateLimitedError,
    VoiceUnavailableError,
    VoiceUpstreamError,
)

Kind = Literal["tts", "stt", "voices"]

#: ``detail.status`` values read from an error body; anything else is ignored.
KNOWN_STATUS = frozenset(
    {
        "quota_exceeded",
        "invalid_api_key",
        "missing_permissions",
        "voice_not_found",
        "voice_does_not_exist",
        "invalid_uid",
        "too_many_concurrent_requests",
        "system_busy",
        "detected_unusual_activity",
    }
)
NOT_FOUND = frozenset({"voice_not_found", "voice_does_not_exist", "invalid_uid"})


def classify(status: int, detail: str | None, kind: Kind) -> VoiceError:
    """The stable error for an HTTP ``status`` (and the body's ``detail.status``)."""
    if detail == "quota_exceeded" or status == 402:
        return VoiceQuotaError("the voice account has no allowance left", status=status)
    if status in (401, 403):
        if detail == "missing_permissions":
            return VoicePermissionMissingError(
                "the voice key lacks a permission this call needs", status=status
            )
        if detail == "detected_unusual_activity":
            return VoiceUnavailableError(
                "the voice account is restricted", status=status
            )
        return VoiceKeyInvalidError(
            "the voice provider rejected the key", status=status
        )
    if status == 413:
        return VoiceLimitError("the voice provider refused the request as too large")
    if status == 404 or detail in NOT_FOUND:
        if kind == "tts":
            return VoiceNotFoundError("the voice id is unknown", status=status)
        return VoiceUnavailableError(
            "the voice provider has no such resource", status=status
        )
    if status == 422:
        if kind == "tts":
            return VoiceNotFoundError(
                "the voice request was not accepted", status=status
            )
        return VoiceInputError("the provider did not accept the audio")
    if status == 429:
        return VoiceRateLimitedError(
            "the voice provider is rate limiting", status=status
        )
    if status >= 500 or status == 408:
        return VoiceUnavailableError(
            f"the voice provider answered HTTP {status}", status=status
        )
    return VoiceUpstreamError(
        f"the voice provider answered HTTP {status}", status=status
    )


__all__ = ["KNOWN_STATUS", "Kind", "classify"]
