"""OAuth 2.1 error responses (RFC 6749 §5.2).

Every failure an authorization server can report is one of a closed set of
codes. Raising a typed exception rather than returning an ad-hoc dict keeps the
wire format in one place and makes "which HTTP status does this map to" a
property of the error instead of a decision repeated at each call site.
"""

from __future__ import annotations


class OAuthError(Exception):
    """Base class for RFC 6749 §5.2 error responses.

    Attributes:
        error: The RFC error code (``invalid_grant``, ``invalid_client``, …).
        description: Human-readable detail, safe to return to the client.
        status_code: HTTP status the error maps to.
    """

    error: str = "server_error"
    status_code: int = 500

    def __init__(self, description: str = "") -> None:
        super().__init__(description or self.error)
        self.description = description

    def to_dict(self) -> dict[str, str]:
        """Render the RFC 6749 §5.2 JSON body."""
        body = {"error": self.error}
        if self.description:
            body["error_description"] = self.description
        return body


class InvalidRequestError(OAuthError):
    """The request is missing a required parameter, or is otherwise malformed."""

    error = "invalid_request"
    status_code = 400


class InvalidClientError(OAuthError):
    """Client authentication failed (e.g., unknown client, no client authentication included)."""

    # 401 rather than 400: the client failed to authenticate itself.
    error = "invalid_client"
    status_code = 401


class InvalidGrantError(OAuthError):
    """The authorization grant or refresh token is invalid, expired, revoked, or otherwise unusable."""

    error = "invalid_grant"
    status_code = 400


class UnauthorizedClientError(OAuthError):
    """The client is not authorized to use this grant type."""

    error = "unauthorized_client"
    status_code = 400


class UnsupportedGrantTypeError(OAuthError):
    """The authorization server does not support the grant type."""

    error = "unsupported_grant_type"
    status_code = 400


class InvalidScopeError(OAuthError):
    """The requested scope is invalid, unknown, malformed, or exceeds the scope of permissions granted."""

    error = "invalid_scope"
    status_code = 400


class InvalidTargetError(OAuthError):
    """The requested target service (``resource``) is unknown or not permitted.

    Defined by RFC 8693 §2.2.2 for token exchange, and used here for a
    ``resource`` the actor client is not registered for.
    """

    error = "invalid_target"
    status_code = 400


class AccessDeniedError(OAuthError):
    """The resource owner denied the authorization request."""

    error = "access_denied"
    status_code = 403


class ServerError(OAuthError):
    """The authorization server encountered an unexpected condition."""

    error = "server_error"
    status_code = 500
