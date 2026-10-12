"""Typed values and errors for :mod:`baselith.core.mcp.oauth_client`.

Secrets (access and refresh tokens, client credentials, the PKCE verifier) are
:class:`~pydantic.SecretStr`, so a stray ``repr()`` or log line never carries
them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from baselith.core.mcp.client_errors import MCPClientError


class MCPOAuthError(MCPClientError):
    """A discovery, registration or token step failed.

    Attributes:
        error: The RFC 6749 ``error`` code when the server sent one (sanitised
            to a short token), else ``None``.
        error_description: Bounded, redacted ``error_description``. Kept off
            ``str()`` on purpose: it is server-controlled text.
    """

    def __init__(
        self,
        message: str,
        *,
        error: str | None = None,
        error_description: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error = error
        self.error_description = error_description


@dataclass(frozen=True)
class ProtectedResourceMetadata:
    """RFC 9728 protected-resource metadata."""

    resource: str
    authorization_servers: tuple[str, ...]
    scopes_supported: tuple[str, ...] = ()
    bearer_methods_supported: tuple[str, ...] = ()
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class AuthorizationServerMetadata:
    """The slice of RFC 8414 metadata the client needs."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    revocation_endpoint: str | None = None
    scopes_supported: tuple[str, ...] = ()
    code_challenge_methods_supported: tuple[str, ...] = ()
    token_endpoint_auth_methods_supported: tuple[str, ...] = ()
    grant_types_supported: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClientRegistration:
    """An RFC 7591 registration result. Both credentials are secrets."""

    client_id: SecretStr
    client_secret: SecretStr | None = None
    client_secret_expires_at: int | None = None
    redirect_uris: tuple[str, ...] = ()
    token_endpoint_auth_method: str = "none"  # noqa: S105


@dataclass(frozen=True)
class PKCEPair:
    """A PKCE verifier and its S256 challenge (RFC 7636)."""

    verifier: SecretStr
    challenge: str
    method: str = "S256"


@dataclass(frozen=True)
class AuthorizationRequest:
    """What a caller needs to send the user off and to finish the flow later.

    The ``code_verifier`` must be kept server-side with ``state`` and handed
    to :meth:`~baselith.core.mcp.oauth_client.OAuthClient.exchange_code`.
    """

    url: str
    state: str
    code_verifier: SecretStr


@dataclass(frozen=True)
class TokenSet:
    """A token endpoint response.

    Attributes:
        expires_at: Unix time the access token expires, derived from
            ``expires_in`` at receipt, or ``None`` when the server gave none.
    """

    access_token: SecretStr
    token_type: str
    refresh_token: SecretStr | None = None
    expires_in: int | None = None
    expires_at: float | None = None
    scope: str | None = None


__all__ = [
    "AuthorizationRequest",
    "AuthorizationServerMetadata",
    "ClientRegistration",
    "MCPOAuthError",
    "PKCEPair",
    "ProtectedResourceMetadata",
    "TokenSet",
]
