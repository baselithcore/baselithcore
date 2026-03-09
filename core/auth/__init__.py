"""
Authentication and authorization system.

Provides JWT token handling, API key validation, and middleware.
"""

from core.auth.api_keys import APIKeyValidator
from core.auth.jwt import JWTHandler
from core.auth.manager import AuthManager, get_auth_manager
from core.auth.types import (
    AuthError,
    AuthRole,
    AuthUser,
    InsufficientPermissionsError,
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
    "JWTHandler",
    "APIKeyValidator",
    "AuthManager",
    "get_auth_manager",
]


__all__ = [
    "AuthRole",
    "AuthUser",
    "AuthError",
    "TokenExpiredError",
    "InvalidTokenError",
    "InsufficientPermissionsError",
    "JWTHandler",
    "APIKeyValidator",
    "AuthManager",
    "get_auth_manager",
]
