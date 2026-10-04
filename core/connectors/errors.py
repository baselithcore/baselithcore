"""The connector error hierarchy and HTTP status mapping.

One hierarchy for every external system, so callers decide policy by type:
``ConnectorTransientError`` (and its ``ConnectorRateLimitedError`` subclass)
is worth retrying; everything else is permanent until something changes.
Messages are redacted before they are stored, so an exception can be logged,
audited or shown without leaking a credential.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

_BODY_SNIPPET = 200
_REDACTED = "***"


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every non-empty secret in ``text`` with ``***``."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, _REDACTED)
    return text


class ConnectorError(Exception):
    """Base class for every connector failure.

    Attributes:
        connector: Name of the connector that failed.
    """

    def __init__(self, connector: str, message: str) -> None:
        super().__init__(f"[{connector}] {message}")
        self.connector = connector


class ConnectorConfigError(ConnectorError):
    """Missing credentials, an unknown action or connector, a bad spec."""


class ConnectorAuthError(ConnectorError):
    """The external system rejected the credentials (401/403)."""


class ConnectorNotFoundError(ConnectorError):
    """The requested resource does not exist (404)."""


class ConnectorRequestError(ConnectorError):
    """Any other client error (4xx); permanent for this request.

    Attributes:
        status: The HTTP status code.
    """

    def __init__(self, connector: str, message: str, *, status: int) -> None:
        super().__init__(connector, message)
        self.status = status


class ConnectorEgressError(ConnectorError):
    """The SSRF policy refused the target host or address."""


class ConnectorResponseTooLargeError(ConnectorError):
    """The response body exceeded ``ConnectorSpec.max_response_bytes``.

    Permanent for this request: the same call returns the same body, and a
    retry would only pay for the bytes again.
    """


class ConnectorTransientError(ConnectorError):
    """A failure worth retrying: transport error, timeout, 408, 5xx."""


class ConnectorConnectError(ConnectorTransientError):
    """The connection was never established, so the request was never sent.

    Unlike other transient failures this one is safe to retry even for a
    non-idempotent request: the server cannot have acted on it.
    """


class ConnectorRateLimitedError(ConnectorTransientError):
    """The external system throttled the call (429).

    Attributes:
        retry_after: Seconds the server asked to wait, when it said. The
            attribute name is the one ``core.resilience.retry`` honours.
    """

    def __init__(
        self, connector: str, message: str, *, retry_after: float | None = None
    ) -> None:
        super().__init__(connector, message)
        self.retry_after = retry_after


class ConnectorUnavailableError(ConnectorError):
    """The connector's circuit breaker is open; the call was not attempted."""


def parse_retry_after(value: str | None) -> float | None:
    """Parse an RFC 9110 ``Retry-After`` value (delay-seconds or HTTP-date)."""
    if value is None:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    return max(seconds, 0.0)


def error_for_response(
    connector: str, response: httpx.Response, secrets: Iterable[str] = ()
) -> ConnectorError | None:
    """Map a non-success response to the hierarchy; ``None`` for 1xx-3xx."""
    status = response.status_code
    if status < 400:
        return None
    try:
        body = response.text[:_BODY_SNIPPET]
    except httpx.ResponseNotRead:
        body = ""
    message = redact(f"HTTP {status} from {response.url.host}: {body}", secrets)
    if status in (401, 403):
        return ConnectorAuthError(connector, message)
    if status == 404:
        return ConnectorNotFoundError(connector, message)
    if status == 429:
        return ConnectorRateLimitedError(
            connector,
            message,
            retry_after=parse_retry_after(response.headers.get("Retry-After")),
        )
    if status == 408 or status >= 500:
        return ConnectorTransientError(connector, message)
    return ConnectorRequestError(connector, message, status=status)


__all__ = [
    "ConnectorAuthError",
    "ConnectorConfigError",
    "ConnectorConnectError",
    "ConnectorEgressError",
    "ConnectorError",
    "ConnectorNotFoundError",
    "ConnectorRateLimitedError",
    "ConnectorRequestError",
    "ConnectorResponseTooLargeError",
    "ConnectorTransientError",
    "ConnectorUnavailableError",
    "error_for_response",
    "parse_retry_after",
    "redact",
]
