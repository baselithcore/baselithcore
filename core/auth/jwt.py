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
from core.cache.redis_cache import create_redis_client
from core.config.cache import get_redis_cache_config

logger = get_logger(__name__)


class JWTHandler:
    """
    JWT token handler using industry-standard PyJWT library.
    """

    def __init__(
        self,
        secret_key: str,
        algorithm: str = "HS256",
        token_lifetime: int = 3600,  # 1 hour
        refresh_lifetime: int = 86400 * 7,  # 7 days
    ) -> None:
        self._secret_key = secret_key
        self._algorithm = algorithm
        self._token_lifetime = token_lifetime
        self._refresh_lifetime = refresh_lifetime

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

        payload = {
            "sub": user_id,
            "iat": now,
            "exp": now + self._token_lifetime,
            "jti": token_id,
            "roles": [r.value for r in (roles or {AuthRole.USER})],
        }
        if extra_claims:
            payload.update(extra_claims)

        return jwt.encode(payload, self._secret_key, algorithm=self._algorithm)

    def create_refresh_token(self, user_id: str) -> str:
        """Create a refresh token."""
        now = int(time.time())
        payload = {
            "sub": user_id,
            "iat": now,
            "exp": now + self._refresh_lifetime,
            "jti": secrets.token_hex(8),
            "type": "refresh",
        }
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

        new_access = self.create_token(user.user_id, user.roles)
        new_refresh = self.create_refresh_token(user.user_id)

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
        try:
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[self._algorithm],
            )
        except jwt.ExpiredSignatureError as e:
            raise TokenExpiredError("Token has expired") from e
        except jwt.InvalidTokenError as e:
            raise InvalidTokenError(f"Invalid token: {e}") from e

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
