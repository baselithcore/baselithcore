"""
Centralized Identity and Access Management (IAM).

Provides a unified interface for authenticating requests via JWT or API
keys. Implements role-based access control (RBAC) through decorators,
ensuring the 'Sacred Core' remains protected from unauthorized access.
"""

from core.observability.logging import get_logger
import secrets
from typing import Callable, Optional, Set

from core.auth.api_keys import APIKeyValidator
from core.auth.jwt import JWTHandler
from core.auth.types import (
    AuthRole,
    AuthUser,
    InsufficientPermissionsError,
)
from core.config.security import SecurityConfig, get_security_config

logger = get_logger(__name__)


class AuthManager:
    """
    Manager for system-wide authentication and authorization.

    Coordinates between different authentication schemes (JWT, API Keys)
    and provides high-level utilities for identity verification and
    permission checking. Acts as the primary gatekeeper for protected
    resources within the framework.
    """

    def __init__(
        self,
        config: Optional[SecurityConfig] = None,
        secret_key: Optional[str] = None,
        token_lifetime: int = 3600,
    ) -> None:
        """
        Initialize AuthManager.

        Args:
            config: Security configuration (injected)
            secret_key: Optional secret key (legacy/overrides config)
            token_lifetime: Token lifetime in seconds (legacy)
        """
        self._config = config or get_security_config()

        # Determine secret key: explicit arg > config > generate random
        final_secret = secret_key or (
            self._config.secret_key.get_secret_value()
            if self._config.secret_key
            else None
        )
        if not final_secret:
            logger.warning("No SECRET_KEY set in config. Generating transient key.")
            final_secret = secrets.token_hex(32)

        self._jwt = JWTHandler(final_secret, token_lifetime=token_lifetime)
        self._api_keys = APIKeyValidator(config=self._config)

    @property
    def jwt(self) -> JWTHandler:
        """Get JWT handler."""
        return self._jwt

    @property
    def api_keys(self) -> APIKeyValidator:
        """Get API key validator."""
        return self._api_keys

    async def create_token(
        self,
        user_id: str,
        roles: Optional[Set[AuthRole]] = None,
        **extra_claims,
    ) -> str:
        """
        Issue a new JWT access token for a user.

        Args:
            user_id: Unique identifier for the user.
            roles: Set of permissions/roles to embed in the token.
            **extra_claims: Any additional metadata to include in the payload.

        Returns:
            str: Encoded JWT string.
        """
        token = self._jwt.create_token(user_id, roles, extra_claims)
        logger.info(
            f"AUDIT | AUTH | Token issued for user {user_id} with roles {[r.value for r in (roles or [])]}"
        )
        return token

    async def rotate_refresh_token(self, refresh_token: str) -> tuple[str, str]:
        """
        Rotate a refresh token.

        Args:
            refresh_token: Raw JWT refresh token string

        Returns:
            Tuple of (new_access_token, new_refresh_token)
        """
        try:
            tokens = await self._jwt.rotate_refresh_token(refresh_token)
            logger.info("AUDIT | AUTH | Refresh token rotated successfully")
            return tokens
        except Exception as e:
            logger.warning(f"AUDIT | AUTH | Refresh token rotation failed: {e}")
            raise

    async def authenticate(self, auth_header: Optional[str]) -> AuthUser:
        """
        Authenticate a user based on the provided Authorization header.

        Parsing logic handles both standard OIDC Bearer tokens and
        framework-specific API Keys.

        Args:
            auth_header: Raw 'Authorization' header string (e.g., 'Bearer <jwt>').

        Returns:
            AuthUser: A validated identity object. If authentication fails,
                     returns an anonymous user with limited permissions.
        """
        if not auth_header:
            return AuthUser(user_id="anonymous", roles={AuthRole.ANONYMOUS})

        parts = auth_header.split(" ", 1)
        if len(parts) != 2:
            return AuthUser(user_id="anonymous", roles={AuthRole.ANONYMOUS})

        scheme, credential = parts

        if scheme.lower() == "bearer":
            try:
                user = await self._jwt.verify_token(credential)
                logger.info(
                    f"AUDIT | AUTH | JWT Authentication successful for user {user.user_id}"
                )
                return user
            except Exception as e:
                logger.warning(f"AUDIT | AUTH | JWT Authentication failed: {e}")
                return AuthUser(user_id="anonymous", roles={AuthRole.ANONYMOUS})

        elif scheme.lower() == "apikey":
            auth_user = await self._api_keys.validate_key(credential)
            if auth_user:
                logger.info(
                    f"AUDIT | AUTH | API Key Authentication successful for user {auth_user.user_id}"
                )
                return auth_user
            else:
                logger.warning(
                    "AUDIT | AUTH | API Key Authentication failed: Invalid key"
                )

        logger.warning(f"AUDIT | AUTH | Authentication failed: Unknown scheme {scheme}")
        return AuthUser(user_id="anonymous", roles={AuthRole.ANONYMOUS})

    async def revoke_token(self, token: str) -> None:
        """
        Revoke an access token.

        Args:
            token: Raw JWT token string
        """
        await self._jwt.revoke_token(token)
        logger.info("AUDIT | AUTH | Token revoked")

    def require_auth(self, roles: Optional[Set[AuthRole]] = None) -> Callable:
        """
        Decorator to require authentication.

        Args:
            roles: Required roles (any of these)

        Example:
            @auth.require_auth({AuthRole.ADMIN})
            async def admin_only_route(user: AuthUser):
                ...
        """

        def decorator(func: Callable) -> Callable:
            # We assume the decorated function is async or sync?
            import functools
            import inspect

            # Common logic for permission checking
            def _check_permissions(
                user_obj: Optional[AuthUser], required_roles: Optional[Set[AuthRole]]
            ):
                if not user_obj or not user_obj.is_authenticated:
                    raise InsufficientPermissionsError("Authentication required")
                if required_roles and not any(
                    user_obj.has_role(r) for r in required_roles
                ):
                    raise InsufficientPermissionsError(
                        f"Requires one of roles: {[r.value for r in required_roles]}"
                    )

            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args, **kwargs):
                    user = kwargs.get("user") or kwargs.get("current_user")
                    _check_permissions(user, roles)
                    return await func(*args, **kwargs)

                return async_wrapper
            else:

                @functools.wraps(func)
                def sync_wrapper(*args, **kwargs):
                    user = kwargs.get("user") or kwargs.get("current_user")
                    _check_permissions(user, roles)
                    return func(*args, **kwargs)

                return sync_wrapper

        return decorator


# Global instance
_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    """Get or create global auth manager."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager
