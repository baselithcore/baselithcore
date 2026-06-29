"""
Authentication and authorization system.

Provides JWT token handling, API key validation, and middleware.
"""

from core.auth.api_keys import APIKeyValidator
from core.auth.jwt import JWTHandler
from core.auth.manager import AuthManager, get_auth_manager
from core.auth.mfa import (
    MFAEnrollment,
    TOTPProvider,
    generate_recovery_codes,
    generate_secret,
    generate_totp,
    hash_recovery_code,
    provisioning_uri,
    verify_recovery_code,
    verify_totp,
)
from core.auth.oidc import OIDCVerifier
from core.auth.scopes import (
    KNOWN_SCOPES,
    ROLE_SCOPES,
    SUPERUSER_SCOPE,
    effective_scopes,
    expand_roles,
    scope_satisfied,
    scopes_satisfied,
)
from core.auth.types import (
    AuthError,
    AuthRole,
    AuthUser,
    InsufficientPermissionsError,
    InsufficientScopeError,
    InvalidTokenError,
    TokenExpiredError,
)

__all__ = [
    "AuthRole",
    "AuthUser",
    "AuthError",
    "TokenExpiredError",
    "InvalidTokenError",
    "InsufficientPermissionsError",
    "InsufficientScopeError",
    "JWTHandler",
    "APIKeyValidator",
    "OIDCVerifier",
    "AuthManager",
    "get_auth_manager",
    # Multi-factor authentication (TOTP / RFC 6238)
    "TOTPProvider",
    "MFAEnrollment",
    "generate_secret",
    "generate_totp",
    "verify_totp",
    "provisioning_uri",
    "generate_recovery_codes",
    "hash_recovery_code",
    "verify_recovery_code",
    # Scopes / capability-based authorization
    "KNOWN_SCOPES",
    "ROLE_SCOPES",
    "SUPERUSER_SCOPE",
    "effective_scopes",
    "expand_roles",
    "scope_satisfied",
    "scopes_satisfied",
]
