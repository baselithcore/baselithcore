"""
Authentication and authorization system.

Provides JWT token handling, API key validation, and middleware.
"""

from baselith.core.auth.api_keys import APIKeyValidator
from baselith.core.auth.jwt import JWTHandler
from baselith.core.auth.manager import AuthManager, get_auth_manager
from baselith.core.auth.mfa import (
    InMemoryTOTPGuard,
    MFAEnrollment,
    TOTPGuard,
    TOTPProvider,
    generate_recovery_codes,
    generate_secret,
    generate_totp,
    hash_recovery_code,
    provisioning_uri,
    verify_recovery_code,
    verify_totp,
    verify_totp_matched_counter,
)
from baselith.core.auth.oidc import OIDCVerifier
from baselith.core.auth.scopes import (
    KNOWN_SCOPES,
    ROLE_SCOPES,
    SUPERUSER_SCOPE,
    effective_scopes,
    expand_roles,
    scope_satisfied,
    scopes_satisfied,
)
from baselith.core.auth.types import (
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
    "TOTPGuard",
    "InMemoryTOTPGuard",
    "MFAEnrollment",
    "generate_secret",
    "generate_totp",
    "verify_totp",
    "verify_totp_matched_counter",
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
