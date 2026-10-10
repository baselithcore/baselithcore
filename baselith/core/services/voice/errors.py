"""Typed voice errors.

Every error carries a stable ``code`` for callers to branch on and a message that
is safe to log or show: it never embeds a response body, a header or a credential
(text a third party wrote is passed through :func:`baselith.core.connectors.errors.redact`
and bounded before it is allowed anywhere near a message).
"""

from __future__ import annotations

from collections.abc import Iterable

from baselith.core.connectors.errors import redact

_MAX_MESSAGE = 200


def safe_text(text: str, secrets: Iterable[str] = ()) -> str:
    """``text`` redacted, single-line and bounded; for messages and logs only."""
    return redact(" ".join(text.split()), secrets)[:_MAX_MESSAGE]


class VoiceError(Exception):
    """Base of every voice failure.

    Attributes:
        code: Stable machine-readable code (``not_configured``, ``too_large``...).
    """

    code = "voice_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(safe_text(message) or self.code)


class VoiceNotConfiguredError(VoiceError):
    """No usable provider or endpoint is configured for this operation."""

    code = "not_configured"


class VoiceEgressError(VoiceError):
    """The SSRF policy refused the configured endpoint."""

    code = "egress_blocked"


class VoiceLimitError(VoiceError):
    """A size cap (request audio, response body or text length) was exceeded."""

    code = "too_large"


class VoiceUpstreamError(VoiceError):
    """The provider answered with an error or an unusable body.

    Attributes:
        status: The HTTP status, when there was one.
    """

    code = "upstream_failed"

    def __init__(self, message: str = "", *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class VoiceProviderError(VoiceUpstreamError):
    """A provider failure that was understood, so it carries a stable, specific code.

    Subclasses of :class:`VoiceUpstreamError`: code that only knows the generic
    ``upstream_failed`` keeps working, code that wants to word the cause reads ``code``.
    """


class VoiceKeyInvalidError(VoiceProviderError):
    """The provider rejected the API key (401/403)."""

    code = "key_invalid"


class VoicePermissionMissingError(VoiceProviderError):
    """The key is valid but lacks a permission the call needs (e.g. Speech to Text)."""

    code = "permission_missing"


class VoiceQuotaError(VoiceProviderError):
    """The account's character or credit allowance is used up."""

    code = "quota_exceeded"


class VoiceNotFoundError(VoiceProviderError):
    """The voice id is unknown to the provider (or not usable by the account)."""

    code = "voice_not_found"


class VoiceRateLimitedError(VoiceProviderError):
    """The provider is throttling the account (429), retries exhausted."""

    code = "rate_limited"


class VoiceUnavailableError(VoiceProviderError):
    """The provider is down, overloaded, or answered something unusable (5xx, redirect)."""

    code = "provider_unavailable"


class VoiceTimeoutError(VoiceError):
    """The provider did not answer within the deadline."""

    code = "timeout"


class VoiceInputError(VoiceError):
    """The caller's input is unusable (empty audio, empty text)."""

    code = "invalid_input"


__all__ = [
    "VoiceEgressError",
    "VoiceError",
    "VoiceInputError",
    "VoiceKeyInvalidError",
    "VoiceLimitError",
    "VoiceNotConfiguredError",
    "VoiceNotFoundError",
    "VoicePermissionMissingError",
    "VoiceProviderError",
    "VoiceQuotaError",
    "VoiceRateLimitedError",
    "VoiceTimeoutError",
    "VoiceUnavailableError",
    "VoiceUpstreamError",
    "safe_text",
]
