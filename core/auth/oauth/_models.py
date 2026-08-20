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
    TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"


#: The only ``subject_token_type``/``requested_token_type`` this server handles.
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


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
    #: Client ids permitted to exchange this client's tokens (RFC 8693
    #: delegation). Empty means no agent may act for this client's users.
    allowed_actors: frozenset[str] = frozenset()


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


@dataclass(frozen=True)
class TokenExchangeRequest:
    """A parsed ``grant_type=token-exchange`` request (RFC 8693 §2.1)."""

    subject_token: str
    subject_token_type: str
    requested_token_type: str | None
    scope: frozenset[str]
    resource: str | None = None


@dataclass(frozen=True)
class SubjectTokenContext:
    """What the protocol layer needs to know about a verified subject token.

    Deliberately not the raw claim dict: verifying the JWT is a plugin concern
    (it needs the key store), while deciding whether the delegation is allowed
    is pure protocol.
    """

    subject: str
    client_id: str
    scope: frozenset[str]
    tenant_id: str | None
    audience: str | None
    has_actor: bool
