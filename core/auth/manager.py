"""
Centralized Identity and Access Management (IAM).

Provides a unified interface for authenticating requests via JWT or API
keys. Implements role-based access control (RBAC) through decorators,
ensuring the 'Sacred Core' remains protected from unauthorized access.
"""

from collections.abc import Callable

from pydantic import SecretStr

from core.auth.api_keys import APIKeyValidator
from core.auth.jwt import JWTHandler
from core.auth.mfa import TOTPProvider
from core.auth.oidc import OIDCVerifier
from core.auth.types import (
    AuthRole,
    AuthUser,
    InsufficientPermissionsError,
    InsufficientScopeError,
)
from core.config.security import SecurityConfig, get_security_config
from core.observability.logging import get_logger

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
        config: SecurityConfig | None = None,
        secret_key: str | None = None,
        token_lifetime: int | None = None,
    ) -> None:
        """
        Initialize AuthManager.

        Args:
            config: Security configuration (injected)
            secret_key: Optional secret key (legacy/overrides config)
            token_lifetime: Access-token lifetime in seconds. When ``None``
                (the default), it is read from
                ``SecurityConfig.access_token_lifetime`` (env
                ``AUTH_ACCESS_TOKEN_LIFETIME`` / ``AUTH_SESSION_LIFETIME``,
                default 3600) so operator config actually governs the issued
                token ``exp``. An explicit value overrides the config.
        """
        self._config = config or get_security_config()
        if token_lifetime is None:
            token_lifetime = int(getattr(self._config, "access_token_lifetime", 3600))

        # Determine secret key: explicit arg > config. Keep it wrapped in
        # SecretStr where possible so the plaintext is not unwrapped until it
        # reaches JWTHandler (avoids leaking via repr/tracebacks here).
        final_secret: str | SecretStr | None
        if secret_key:
            final_secret = secret_key
        elif self._config.secret_key:
            final_secret = self._config.secret_key
        else:
            final_secret = None
        if not final_secret:
            raise ValueError(
                "SECRET_KEY is not configured. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )

        self._jwt = JWTHandler(
            final_secret,
            token_lifetime=token_lifetime,
            issuer=getattr(self._config, "jwt_issuer", None),
            audience=getattr(self._config, "jwt_audience", None),
            strict_validation=bool(
                getattr(self._config, "jwt_strict_validation", False)
            ),
        )
        self._api_keys = APIKeyValidator(config=self._config)
        # Federated SSO. Inert unless OIDC_ENABLED + issuer/audience are set.
        self._oidc = OIDCVerifier(config=self._config)
        # TOTP second factor (NIS2 Art. 21(2)(j)). Lazily built — only callers
        # that opt into MFA touch it; existing auth paths are unaffected.
        self._mfa: TOTPProvider | None = None

    @property
    def jwt(self) -> JWTHandler:
        """Get JWT handler."""
        return self._jwt

    @property
    def api_keys(self) -> APIKeyValidator:
        """Get API key validator."""
        return self._api_keys

    @property
    def oidc(self) -> OIDCVerifier:
        """Get the OIDC verifier (inert unless configured)."""
        return self._oidc

    @property
    def mfa(self) -> TOTPProvider:
        """Get the TOTP multi-factor provider, configured with the deployment issuer.

        Use to enroll a user (``mfa.enroll(account)``) and to verify a step-up
        code or recovery code at login. The provider is stateless; persisting
        the enrollment secret (encrypted) and recovery-code hashes is the
        application's responsibility. See ``MFA_ENABLED`` / ``MFA_ISSUER``.
        """
        if self._mfa is None:
            self._mfa = TOTPProvider(
                issuer=getattr(self._config, "mfa_issuer", "BaselithCore")
            )
        return self._mfa

    @property
    def mfa_enabled(self) -> bool:
        """Whether MFA is switched on for this deployment (``MFA_ENABLED``)."""
        return bool(getattr(self._config, "mfa_enabled", False))

    async def _verify_bearer(self, credential: str) -> AuthUser:
        """Verify a bearer token: local HS256 first, OIDC fallback if enabled.

        Returns an anonymous user if neither path accepts the token. Local
        verification is tried first so the common (self-issued) token path stays
        a single in-process check with no network dependency.
        """
        try:
            user = await self._jwt.verify_token(credential)
            logger.info(
                f"AUDIT | AUTH | JWT Authentication successful for user {user.user_id}"
            )
            return user
        except Exception as local_exc:
            if not self._oidc.is_configured:
                # Log only the exception class — messages may include token bytes.
                logger.warning(
                    "AUDIT | AUTH | JWT Authentication failed: %s",
                    type(local_exc).__name__,
                )
                return AuthUser(user_id="anonymous", roles={AuthRole.ANONYMOUS})

        # Local verification failed and OIDC is configured — try the IdP.
        try:
            user = await self._oidc.verify(credential)
            logger.info(
                f"AUDIT | AUTH | OIDC Authentication successful for user {user.user_id}"
            )
            return user
        except Exception as oidc_exc:
            logger.warning(
                "AUDIT | AUTH | Bearer Authentication failed (local+OIDC): %s",
                type(oidc_exc).__name__,
            )
            return AuthUser(user_id="anonymous", roles={AuthRole.ANONYMOUS})

    async def create_token(
        self,
        user_id: str,
        roles: set[AuthRole] | None = None,
        scopes: set[str] | None = None,
        lifetime: int | None = None,
        **extra_claims,
    ) -> str:
        """
        Issue a new JWT access token for a user.

        Args:
            user_id: Unique identifier for the user.
            roles: Set of permissions/roles to embed in the token.
            scopes: Explicit capability scopes to embed (``resource:action``).
            lifetime: Per-token access lifetime in seconds. Overrides the
                handler default. Use this for bounded-TTL tokens (e.g.
                impersonation) — passing ``exp`` via ``extra_claims`` is a no-op
                because ``exp`` is a stripped reserved claim.
            **extra_claims: Any additional metadata to include in the payload.

        Returns:
            str: Encoded JWT string.
        """
        token = self._jwt.create_token(
            user_id, roles, extra_claims, scopes=scopes, lifetime=lifetime
        )
        logger.info(
            f"AUDIT | AUTH | Token issued for user {user_id} with roles "
            f"{[r.value for r in (roles or [])]} scopes {sorted(scopes or [])}"
        )
        return token

    @staticmethod
    def enforce_scopes(
        user: AuthUser | None,
        *required: str,
        require_all: bool = True,
    ) -> None:
        """Raise unless ``user`` is authenticated and holds the required scopes.

        Use at any choke point (route handler, tool gate, service call) to
        enforce least-privilege capability checks independent of HTTP routing.

        Args:
            user: The authenticated identity (``None`` → unauthenticated).
            *required: Scopes demanded (``resource:action``).
            require_all: When ``True`` every scope must be held; otherwise any.

        Raises:
            InsufficientScopeError: If unauthenticated or missing capability.
        """
        if not user or not user.is_authenticated:
            raise InsufficientScopeError(
                "Authentication required", required=set(required)
            )
        if required and not user.has_scopes(*required, require_all=require_all):
            raise InsufficientScopeError(
                f"Requires scope(s): {sorted(required)}", required=set(required)
            )

    def require_scopes(self, *required: str, require_all: bool = True) -> Callable:
        """
        Decorator enforcing capability scopes on a route/handler.

        The wrapped function must receive the identity as a ``user`` or
        ``current_user`` keyword argument (same convention as
        :meth:`require_auth`).

        Example:
            @auth.require_scopes("webhooks:write")
            async def create_webhook(user: AuthUser):
                ...
        """

        def decorator(func: Callable) -> Callable:
            import functools
            import inspect

            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args, **kwargs):
                    user = kwargs.get("user") or kwargs.get("current_user")
                    self.enforce_scopes(user, *required, require_all=require_all)
                    return await func(*args, **kwargs)

                return async_wrapper

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                user = kwargs.get("user") or kwargs.get("current_user")
                self.enforce_scopes(user, *required, require_all=require_all)
                return func(*args, **kwargs)

            return sync_wrapper

        return decorator

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

    async def authenticate(self, auth_header: str | None) -> AuthUser:
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
            return await self._verify_bearer(credential)

        elif scheme.lower() == "apikey":
            # Honor the API_KEY_ENABLED switch: when disabled, do not accept API
            # keys at all (previously the flag was declared but never checked, so
            # keys stayed active even when an operator turned them "off").
            if not getattr(self._config, "api_key_enabled", True):
                logger.warning(
                    "AUDIT | AUTH | API key rejected: API_KEY_ENABLED is false"
                )
                return AuthUser(user_id="anonymous", roles={AuthRole.ANONYMOUS})
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

    def require_auth(self, roles: set[AuthRole] | None = None) -> Callable:
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
                user_obj: AuthUser | None, required_roles: set[AuthRole] | None
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
_auth_manager: AuthManager | None = None


def get_auth_manager() -> AuthManager:
    """Get or create global auth manager."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager
