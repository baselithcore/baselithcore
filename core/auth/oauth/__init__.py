"""OAuth 2.1 authorization-server protocol layer.

Pure protocol: no database, no HTTP framework, no user interface. The stateful
half — client registry, code storage, key management, routes — lives in the
``auth`` plugin. Note the direction of this package: ``core.auth.oidc`` is the
*relying party* side and verifies tokens minted elsewhere, while this package is
the *authorization server* side and mints tokens others verify.
"""

from __future__ import annotations

from core.auth.oauth._errors import (
    AccessDeniedError,
    InvalidClientError,
    InvalidGrantError,
    InvalidRequestError,
    InvalidScopeError,
    InvalidTargetError,
    OAuthError,
    ServerError,
    UnauthorizedClientError,
    UnsupportedGrantTypeError,
)
from core.auth.oauth._exchange import (
    build_actor_claim,
    build_may_act_claim,
    resolve_exchange_scope,
    validate_delegation,
    validate_exchange_request,
)
from core.auth.oauth._grants import (
    assert_grant_allowed,
    resolve_scope,
    validate_authorization_request,
    validate_redirect_uri,
)
from core.auth.oauth._jwks import build_jwks_document
from core.auth.oauth._metadata import build_metadata_document
from core.auth.oauth._models import (
    ACCESS_TOKEN_TYPE,
    AuthorizationRequest,
    ClientType,
    GrantType,
    OAuthClient,
    SubjectTokenContext,
    TokenExchangeRequest,
)
from core.auth.oauth._pkce import S256, derive_code_challenge, verify_code_challenge

__all__ = [
    "ACCESS_TOKEN_TYPE",
    "S256",
    "AccessDeniedError",
    "AuthorizationRequest",
    "ClientType",
    "GrantType",
    "InvalidClientError",
    "InvalidGrantError",
    "InvalidRequestError",
    "InvalidScopeError",
    "InvalidTargetError",
    "OAuthClient",
    "OAuthError",
    "ServerError",
    "SubjectTokenContext",
    "TokenExchangeRequest",
    "UnauthorizedClientError",
    "UnsupportedGrantTypeError",
    "assert_grant_allowed",
    "build_actor_claim",
    "build_jwks_document",
    "build_may_act_claim",
    "build_metadata_document",
    "derive_code_challenge",
    "resolve_exchange_scope",
    "resolve_scope",
    "validate_authorization_request",
    "validate_delegation",
    "validate_exchange_request",
    "validate_redirect_uri",
    "verify_code_challenge",
]
