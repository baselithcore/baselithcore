"""Typed exception hierarchy for the BaselithCore SDK.

Errors raised by the client map HTTP failures onto Python exceptions and parse
the server's standardized error envelope::

    {"error": {"code": "...", "message": "...", "type": "...", "request_id": "..."}}

so callers get a stable ``code`` and a ``request_id`` for support correlation.
"""

from __future__ import annotations

from typing import Any


class BaselithError(Exception):
    """Base class for every error raised by the SDK."""


class BaselithConfigError(BaselithError):
    """Client was constructed with an invalid configuration."""


class BaselithAPIError(BaselithError):
    """An error response was returned by the API.

    Attributes:
        status_code: HTTP status of the response.
        code: Stable, machine-readable error code from the envelope (if any).
        message: Human-readable message.
        error_type: The server-side exception class name (if any).
        request_id: Correlation id echoed by the server (``X-Request-ID``).
        body: The raw parsed response body, for debugging.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
        error_type: str | None = None,
        request_id: str | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.error_type = error_type
        self.request_id = request_id
        self.body = body

    def __str__(self) -> str:
        rid = f" (request_id={self.request_id})" if self.request_id else ""
        code = f" [{self.code}]" if self.code else ""
        return f"HTTP {self.status_code}{code}: {self.message}{rid}"


class AuthenticationError(BaselithAPIError):
    """401 — missing or invalid credentials."""


class PermissionError_(BaselithAPIError):
    """403 — authenticated but lacking the required role/scope."""


class NotFoundError(BaselithAPIError):
    """404 — resource does not exist."""


class RateLimitError(BaselithAPIError):
    """429 — rate limit exceeded.

    ``retry_after`` carries the server's ``Retry-After`` hint in seconds when
    present.
    """

    def __init__(self, *args: Any, retry_after: float | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class ServerError(BaselithAPIError):
    """5xx — the server failed to handle the request."""


class APIConnectionError(BaselithError):
    """The request never reached the server (network/timeout)."""


def error_from_response(
    status_code: int,
    body: Any,
    *,
    request_id: str | None = None,
    retry_after: float | None = None,
) -> BaselithAPIError:
    """Build the most specific :class:`BaselithAPIError` for a response.

    Parses the standardized envelope when present, falling back to a plain
    string body or the status reason.
    """
    code: str | None = None
    error_type: str | None = None
    message = f"request failed with status {status_code}"

    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = err.get("code")
            error_type = err.get("type")
            message = err.get("message") or message
            request_id = err.get("request_id") or request_id
        elif "detail" in body:  # FastAPI HTTPException shape
            detail = body["detail"]
            message = detail if isinstance(detail, str) else str(detail)
    elif isinstance(body, str) and body:
        message = body

    cls: type[BaselithAPIError]
    if status_code == 401:
        cls = AuthenticationError
    elif status_code == 403:
        cls = PermissionError_
    elif status_code == 404:
        cls = NotFoundError
    elif status_code == 429:
        return RateLimitError(
            message,
            status_code=status_code,
            code=code,
            error_type=error_type,
            request_id=request_id,
            body=body,
            retry_after=retry_after,
        )
    elif status_code >= 500:
        cls = ServerError
    else:
        cls = BaselithAPIError

    return cls(
        message,
        status_code=status_code,
        code=code,
        error_type=error_type,
        request_id=request_id,
        body=body,
    )
