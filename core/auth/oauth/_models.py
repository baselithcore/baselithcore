"""Value types shared by the OAuth protocol layer.

Frozen dataclasses, not Pydantic models: these cross no I/O boundary and carry
no validation beyond what the grant rules already enforce, so immutability and
hashability are worth more here than parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ClientType(StrEnum):
    """Whether a client can keep a secret (RFC 6749 §2.1)."""

    PUBLIC = "public"
    CONFIDENTIAL = "confidential"


class GrantType(StrEnum):
    """Grant types this authorization server issues tokens for."""

    AUTHORIZATION_CODE = "authorization_code"
    REFRESH_TOKEN = "refresh_token"
    CLIENT_CREDENTIALS = "client_credentials"
    DEVICE_CODE = "urn:ietf:params:oauth:grant-type:device_code"


@dataclass(frozen=True)
class OAuthClient:
    """A registered client, as the protocol layer sees it.

    Persistence details (secret hash, timestamps, audit columns) stay in the
    plugin; this is only what grant validation needs.
    """

    client_id: str
    client_type: ClientType
    redirect_uris: tuple[str, ...]
    grant_types: frozenset[GrantType]
    allowed_scopes: frozenset[str]
    first_party: bool = False
    tenant_id: str | None = None


@dataclass(frozen=True)
class AuthorizationRequest:
    """A validated ``GET /oauth/authorize`` request awaiting user consent."""

    client_id: str
    redirect_uri: str
    scope: frozenset[str]
    state: str
    code_challenge: str
    code_challenge_method: str
    resource: str | None = None
