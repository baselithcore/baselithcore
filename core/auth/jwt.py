"""
JWT token handling.
"""

from core.observability.logging import get_logger
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

import jwt

from core.auth.types import (
    AuthRole,
    AuthUser,
    InvalidTokenError,
    TokenExpiredError,
)
from pydantic import SecretStr

from core.cache.redis_cache import create_redis_client
from core.config.cache import get_redis_cache_config

logger = get_logger(__name__)

# Signing algorithms that are never acceptable: "none" disables signature
# verification entirely (the classic JWT downgrade attack).
_FORBIDDEN_ALGORITHMS = frozenset({"none", ""})


class JWTHandler:
    """
    JWT token handler using industry-standard PyJWT library.
    """

    def __init__(
        self,
        secret_key: str | SecretStr,
        algorithm: str = "HS256",
        token_lifetime: int = 3600,  # 1 hour
        refresh_lifetime: int = 86400 * 7,  # 7 days
        issuer: Optional[str] = None,
        audience: Optional[str] = None,
        strict_validation: bool = False,
    ) -> None:
        # Accept SecretStr so callers can keep the key wrapped (no plaintext in
        # tracebacks/Sentry frames) and unwrap only here at the last moment.
        self._secret_key = (
            secret_key.get_secret_value()
            if isinstance(secret_key, SecretStr)
            else secret_key
        )
        if algorithm.strip().lower() in _FORBIDDEN_ALGORITHMS:
            raise ValueError(
                f"JWT algorithm {algorithm!r} is not allowed: it disables "
                "signature verification. Use HS256/RS256/ES256/EdDSA."
            )
        self._algorithm = algorithm
        self._token_lifetime = token_lifetime
        self._refresh_lifetime = refresh_lifetime
        self._issuer = issuer
        self._audience = audience
        # When True, verify_token rejects tokens missing aud/iss claims even if
        # not configured on the handler. Recommended for multi-region deployments
        # to prevent cross-cluster token acceptance. Opt-in via env JWT_STRICT_VALIDATION.
        self._strict_validation = strict_validation
        if strict_validation and not (issuer and audience):
            logger.warning(
                "jwt_strict_validation_enabled_without_iss_aud",
                extra={
                    "issuer_configured": bool(issuer),
                    "audience_configured": bool(audience),
                },
            )

        config = get_redis_cache_config()
        self._redis = create_redis_client(config.url)
        self._blacklist_prefix = config.cache_prefix + ":jwt_blacklist:"

    def create_token(
        self,
        user_id: str,
        roles: Optional[Set[AuthRole]] = None,
        extra_claims: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Create an access token.

        Args:
            user_id: User identifier
            roles: User roles
            extra_claims: Additional token claims

        Returns:
            Encoded token string
        """
        now = int(time.time())
        token_id = secrets.token_hex(8)

        payload: Dict[str, Any] = {
            "sub": user_id,
            "iat": now,
            "exp": now + self._token_lifetime,
            "jti": token_id,
            "roles": [r.value for r in (roles or {AuthRole.USER})],
        }
        if self._issuer:
            payload["iss"] = self._issuer
        if self._audience:
            payload["aud"] = self._audience
        if extra_claims:
            payload.update(extra_claims)

        return jwt.encode(payload, self._secret_key, algorithm=self._algorithm)

    def create_refresh_token(
        self,
        user_id: str,
        roles: Optional[Set[AuthRole]] = None,
        tenant_id: Optional[str] = None,
        extra_claims: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create a refresh token, optionally preserving auth context."""
        now = int(time.time())
        payload: Dict[str, Any] = {
            "sub": user_id,
            "iat": now,
            "exp": now + self._refresh_lifetime,
            "jti": secrets.token_hex(8),
            "type": "refresh",
        }
        if roles:
            payload["roles"] = [r.value for r in roles]
        if tenant_id:
            payload["tenant_id"] = tenant_id
        if self._issuer:
            payload["iss"] = self._issuer
        if self._audience:
            payload["aud"] = self._audience
        if extra_claims:
            payload.update(extra_claims)
        return jwt.encode(payload, self._secret_key, algorithm=self._algorithm)

    async def rotate_refresh_token(self, refresh_token: str) -> tuple[str, str]:
        """
        Consume a refresh token, revoke it, and return a new (access_token, refresh_token) pair.

        Raises:
            InvalidTokenError: If token is invalid or not a refresh token
            TokenExpiredError: If token is expired
        """
        user = await self.verify_token(refresh_token)
        if user.metadata.get("type") != "refresh":
            raise InvalidTokenError("Provided token is not a refresh token")

        await self.revoke_token(refresh_token)

        extra_claims: Dict[str, Any] = {}
        if "tenant_id" in user.metadata:
            extra_claims["tenant_id"] = user.metadata["tenant_id"]

        new_access = self.create_token(
            user.user_id,
            user.roles,
            extra_claims=extra_claims or None,
        )
        new_refresh = self.create_refresh_token(
            user.user_id,
            roles=user.roles,
            tenant_id=user.metadata.get("tenant_id"),
        )

        return new_access, new_refresh

    async def revoke_token(self, token: str) -> None:
        """
        Revoke a token by adding its jti to the Redis blacklist.

        Args:
            token: Encoded token string
        """
        try:
            # Decode without verifying expiration to revoke already-expired tokens gracefully
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[self._algorithm],
                options={"verify_exp": False},
            )
        except jwt.InvalidTokenError:
            return  # Ignore completely invalid tokens

        jti = payload.get("jti")
        exp = payload.get("exp")

        if jti and exp:
            now = int(time.time())
            ttl = int(exp) - now
            # Security assumption: already-expired tokens (ttl <= 0) are NOT
            # added to the blacklist because verify_token always calls jwt.decode
            # with verify_exp=True (the default), which will raise ExpiredSignatureError
            # before the blacklist is even consulted. Skipping the setex avoids
            # storing entries with a zero/negative TTL that Redis would reject or
            # immediately evict anyway. If this assumption ever changes (e.g. a
            # code path that verifies tokens with verify_exp=False), this method
            # must be updated to also blacklist expired tokens.
            if ttl > 0:
                await self._redis.setex(self._blacklist_prefix + jti, ttl, b"1")

    async def verify_token(self, token: str) -> AuthUser:
        """
        Verify and decode a token.

        Args:
            token: Encoded token string

        Returns:
            AuthUser with decoded claims

        Raises:
            TokenExpiredError: If token expired
            InvalidTokenError: If token is invalid
        """
        decode_options: Dict[str, Any] = {}
        if self._audience:
            decode_options["audience"] = self._audience
        if self._issuer:
            decode_options["issuer"] = self._issuer

        try:
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[self._algorithm],
                **decode_options,
            )
        except jwt.ExpiredSignatureError as e:
            raise TokenExpiredError("Token has expired") from e
        except jwt.InvalidTokenError as e:
            raise InvalidTokenError(f"Invalid token: {e}") from e

        if self._strict_validation:
            if not payload.get("aud"):
                raise InvalidTokenError("Token missing required 'aud' claim")
            if not payload.get("iss"):
                raise InvalidTokenError("Token missing required 'iss' claim")

        jti = payload.get("jti")
        if jti:
            is_blacklisted = await self._redis.get(self._blacklist_prefix + jti)
            if is_blacklisted:
                raise InvalidTokenError("Token has been revoked")

        # Build AuthUser
        roles = {AuthRole(r) for r in payload.get("roles", ["user"])}
        return AuthUser(
            user_id=payload["sub"],
            roles=roles,
            token_id=payload.get("jti"),
            # Extract tenant_id from payload, default to "default" if not present
            tenant_id=payload.get("tenant_id", "default"),
            expires_at=datetime.fromtimestamp(
                payload.get("exp", time.time()), tz=timezone.utc
            ),
            metadata=payload,
        )
