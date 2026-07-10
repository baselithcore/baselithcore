"""
JWT token handling.
"""

import hashlib
import secrets
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

import jwt
from jwt.algorithms import requires_cryptography
from pydantic import SecretStr

from core.auth.types import (
    AuthRole,
    AuthUser,
    InvalidTokenError,
    TokenExpiredError,
)
from core.cache.redis_cache import create_redis_client
from core.config.cache import get_redis_cache_config
from core.observability.logging import get_logger

logger = get_logger(__name__)

# Signing algorithms that are never acceptable: "none" disables signature
# verification entirely (the classic JWT downgrade attack).
_FORBIDDEN_ALGORITHMS = frozenset({"none", ""})

# Claims that carry security meaning and must be derived from the handler's own
# parameters, never from caller-supplied ``extra_claims``. Without this guard an
# ``extra_claims`` dict (potentially built from user-influenced data) could
# override ``roles``/``exp``/``type``/``sub`` after they were set — minting a
# token with elevated privileges, an extended lifetime, or a forged token type.
# ``tenant_id`` is intentionally NOT reserved: it is legitimate application data
# that refresh-token rotation threads through ``extra_claims``. ``family`` IS
# reserved: it chains a refresh token to its rotation lineage for theft
# detection, so a caller-supplied value could graft a token onto (or detach it
# from) another lineage.
_RESERVED_CLAIMS = frozenset(
    {
        "sub",
        "exp",
        "iat",
        "nbf",
        "jti",
        "iss",
        "aud",
        "roles",
        "scopes",
        "type",
        "family",
    }
)


def _sanitize_extra_claims(
    extra_claims: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Drop reserved (security-bearing) keys from caller-supplied extra claims."""
    if not extra_claims:
        return extra_claims
    safe = {k: v for k, v in extra_claims.items() if k not in _RESERVED_CLAIMS}
    dropped = extra_claims.keys() - safe.keys()
    if dropped:
        logger.warning(
            "jwt_extra_claims_reserved_keys_dropped",
            dropped=sorted(dropped),
        )
    return safe


# Upper bound for the in-process verify cache. A successful verification is
# cached for at most this many seconds (and never past the token's own exp), so
# repeated authenticated requests skip the signature check and the Redis
# blacklist round-trip. The short window bounds revocation staleness: a token
# revoked via revoke_token may still be accepted for up to this long.
_VERIFY_CACHE_MAX_TTL = 5.0

# Hard cap on the number of cached verifications. Entries expire after at most
# _VERIFY_CACHE_MAX_TTL seconds but are only evicted lazily on access/revoke, so
# without a ceiling a flood of distinct valid tokens (rotation, token spray)
# could grow the dict unbounded between sweeps. When full, the oldest entry is
# evicted (LRU). 8192 ≈ a few hundred KB of AuthUser refs — generous for the
# 5-second window while still bounding worst-case memory.
_VERIFY_CACHE_MAX_ENTRIES = 8192


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
        issuer: str | None = None,
        audience: str | None = None,
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

        # For asymmetric algorithms (RS256/ES256/EdDSA/...), PyJWT re-parses the
        # PEM into a key object on every decode. Parse it once here and reuse the
        # prepared key. HMAC algorithms keep using the raw shared secret string.
        self._verify_key: Any = self._secret_key
        if self._algorithm in requires_cryptography:
            try:
                self._verify_key = jwt.get_algorithm_by_name(
                    self._algorithm
                ).prepare_key(self._secret_key)
            except Exception:  # pragma: no cover - defensive
                # Fall back to per-call parsing if pre-parsing fails (e.g. the
                # configured key is the signing/private key form); correctness
                # is preserved, only the optimization is skipped.
                logger.warning(
                    "jwt_verify_key_preparse_failed", algorithm=self._algorithm
                )
                self._verify_key = self._secret_key

        # Tiny TTL cache for successful verifications, keyed on a sha256 hash of
        # the raw token (never the token itself, to avoid storing credentials in
        # memory). Maps token-hash -> (AuthUser, expiry_monotonic).
        self._verify_cache: OrderedDict[str, tuple[AuthUser, float]] = OrderedDict()

        config = get_redis_cache_config()
        self._redis = create_redis_client(config.url)
        self._blacklist_prefix = config.cache_prefix + ":jwt_blacklist:"
        # Revoked refresh-token FAMILIES (rotation lineages). Presenting an
        # already-rotated refresh token is indistinguishable from theft
        # (RFC 9700 §4.14.2), so the whole lineage is revoked — including the
        # thief's freshly rotated descendant.
        self._family_blacklist_prefix = config.cache_prefix + ":jwt_family_blacklist:"

    def create_token(
        self,
        user_id: str,
        roles: set[AuthRole] | None = None,
        extra_claims: dict[str, Any] | None = None,
        scopes: set[str] | None = None,
        lifetime: int | None = None,
    ) -> str:
        """
        Create an access token.

        Args:
            user_id: User identifier
            roles: User roles
            extra_claims: Additional token claims
            scopes: Explicit capability scopes to embed (``resource:action``).
                Optional; role-derived scopes are computed at check time.
            lifetime: Access-token lifetime in seconds for this token only.
                Overrides the handler default (``token_lifetime``). This is a
                first-class parameter because ``exp`` is a reserved claim and is
                stripped from ``extra_claims`` — callers needing a bounded TTL
                (e.g. impersonation) must pass it here, not via ``extra_claims``.
                Values are clamped to at least 1 second; ``None`` uses the default.

        Returns:
            Encoded token string
        """
        now = int(time.time())
        token_id = secrets.token_hex(8)

        effective_lifetime = (
            self._token_lifetime if lifetime is None else max(1, int(lifetime))
        )
        payload: dict[str, Any] = {
            "sub": user_id,
            "iat": now,
            "exp": now + effective_lifetime,
            "jti": token_id,
            "roles": [r.value for r in (roles or {AuthRole.USER})],
        }
        if scopes:
            payload["scopes"] = sorted(scopes)
        if self._issuer:
            payload["iss"] = self._issuer
        if self._audience:
            payload["aud"] = self._audience
        safe_extra = _sanitize_extra_claims(extra_claims)
        if safe_extra:
            payload.update(safe_extra)

        return jwt.encode(payload, self._secret_key, algorithm=self._algorithm)

    def create_refresh_token(
        self,
        user_id: str,
        roles: set[AuthRole] | None = None,
        tenant_id: str | None = None,
        extra_claims: dict[str, Any] | None = None,
        *,
        family: str | None = None,
    ) -> str:
        """Create a refresh token, optionally preserving auth context.

        Args:
            user_id: User identifier.
            roles: Roles to preserve across rotation.
            tenant_id: Tenant to preserve across rotation.
            extra_claims: Additional claims (reserved keys are dropped).
            family: Rotation-lineage id. Internal — ``rotate_refresh_token``
                threads the consumed token's family through so reuse of ANY
                ancestor revokes the whole lineage. A fresh login leaves it
                ``None`` and the new token starts its own family (= its jti).
        """
        now = int(time.time())
        token_id = secrets.token_hex(8)
        payload: dict[str, Any] = {
            "sub": user_id,
            "iat": now,
            "exp": now + self._refresh_lifetime,
            "jti": token_id,
            "type": "refresh",
            "family": family or token_id,
        }
        if roles:
            payload["roles"] = [r.value for r in roles]
        if tenant_id:
            payload["tenant_id"] = tenant_id
        if self._issuer:
            payload["iss"] = self._issuer
        if self._audience:
            payload["aud"] = self._audience
        safe_extra = _sanitize_extra_claims(extra_claims)
        if safe_extra:
            payload.update(safe_extra)
        return jwt.encode(payload, self._secret_key, algorithm=self._algorithm)

    async def rotate_refresh_token(self, refresh_token: str) -> tuple[str, str]:
        """
        Consume a refresh token, revoke it, and return a new (access_token, refresh_token) pair.

        Raises:
            InvalidTokenError: If token is invalid or not a refresh token
            TokenExpiredError: If token is expired
        """
        user = await self.verify_token(refresh_token, expected_type="refresh")

        await self.revoke_token(refresh_token)

        extra_claims: dict[str, Any] = {}
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
            # Preserve the lineage: descendants stay revocable as one family.
            family=user.metadata.get("family"),
        )

        return new_access, new_refresh

    async def revoke_token(self, token: str) -> None:
        """
        Revoke a token by adding its jti to the Redis blacklist.

        Args:
            token: Encoded token string
        """
        # Drop any cached verification for this exact token so revocation is
        # immediate within this process (the short TTL bounds it across others).
        self._verify_cache.pop(hashlib.sha256(token.encode("utf-8")).hexdigest(), None)

        try:
            # Decode without verifying expiration to revoke already-expired tokens gracefully
            payload = jwt.decode(
                token,
                self._verify_key,
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

    @staticmethod
    def _enforce_token_type(user: AuthUser, expected_type: str | None) -> None:
        """Raise ``InvalidTokenError`` if the user's token type is not allowed."""
        if expected_type is None:
            return
        actual = user.metadata.get("type", "access") if user.metadata else "access"
        if actual != expected_type:
            raise InvalidTokenError(
                f"Token type {actual!r} is not valid here (expected {expected_type!r})"
            )

    async def verify_token(
        self, token: str, *, expected_type: str | None = "access"
    ) -> AuthUser:
        """
        Verify and decode a token.

        Args:
            token: Encoded token string
            expected_type: Required value of the token's ``type`` claim. Defaults
                to ``"access"`` so a long-lived **refresh** token cannot be
                presented as a bearer access token (access tokens carry no
                ``type`` claim → treated as ``"access"``). Pass ``"refresh"``
                when consuming a refresh token, or ``None`` to skip the check.

        Returns:
            AuthUser with decoded claims

        Raises:
            TokenExpiredError: If token expired
            InvalidTokenError: If token is invalid or of the wrong type

        Note:
            A successful verification is cached in-process for a short window
            (see ``_VERIFY_CACHE_MAX_TTL``), keyed on a sha256 hash of the raw
            token. Cache hits skip both the signature check and the Redis
            blacklist lookup, so a revocation may take up to that window to take
            effect. The cache never extends past the token's own ``exp``.
        """
        # Cache key is a hash of the token, never the raw token itself, so we do
        # not retain credentials in process memory.
        cache_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = time.monotonic()
        cached = self._verify_cache.get(cache_key)
        if cached is not None:
            user, expiry = cached
            if expiry > now:
                # Enforce the token-type gate on cache hits too — the cache is
                # keyed only on the token, so a refresh token cached during
                # rotation must not be replayable on the access path.
                self._enforce_token_type(user, expected_type)
                # Mark as most-recently-used so the LRU eviction keeps hot
                # tokens and sheds idle ones.
                self._verify_cache.move_to_end(cache_key)
                return user
            # Expired entry: drop it and fall through to a full verification.
            self._verify_cache.pop(cache_key, None)

        decode_options: dict[str, Any] = {}
        if self._audience:
            decode_options["audience"] = self._audience
        if self._issuer:
            decode_options["issuer"] = self._issuer

        try:
            payload = jwt.decode(
                token,
                self._verify_key,
                algorithms=[self._algorithm],
                # A token without `exp` would never expire and could not be
                # blacklisted by revoke_token (which needs exp for the TTL).
                options={"require": ["exp"]},
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
        family = payload.get("family")
        is_refresh = payload.get("type") == "refresh"
        if jti:
            is_blacklisted = await self._redis.get(self._blacklist_prefix + jti)
            if is_blacklisted:
                # A blacklisted REFRESH token being presented again means it was
                # already consumed by rotation — someone (victim or thief) holds
                # a stolen copy (RFC 9700 §4.14.2). Revoke the whole rotation
                # family so the thief's freshly minted descendant dies with it.
                # TTL = refresh lifetime: no descendant can outlive that window.
                if family and is_refresh:
                    logger.warning(
                        "jwt_refresh_reuse_detected_family_revoked", family=family
                    )
                    try:
                        await self._redis.setex(
                            self._family_blacklist_prefix + family,
                            self._refresh_lifetime,
                            b"1",
                        )
                    except Exception:  # pragma: no cover - detection best-effort
                        logger.error("jwt_family_revocation_failed", family=family)
                raise InvalidTokenError("Token has been revoked")
        if family and is_refresh:
            family_revoked = await self._redis.get(
                self._family_blacklist_prefix + family
            )
            if family_revoked:
                raise InvalidTokenError("Token family has been revoked")

        # Build AuthUser
        roles = {AuthRole(r) for r in payload.get("roles", ["user"])}
        scopes = {str(s) for s in payload.get("scopes", [])}
        user = AuthUser(
            user_id=payload["sub"],
            roles=roles,
            scopes=scopes,
            token_id=payload.get("jti"),
            # Extract tenant_id from payload, default to "default" if not present
            tenant_id=payload.get("tenant_id", "default"),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
            metadata=payload,
        )

        # Reject a token whose type does not match the calling context (e.g. a
        # refresh token presented as an access bearer). Enforced before caching
        # so a wrong-type token is never stored as a valid verification.
        self._enforce_token_type(user, expected_type)

        # Cache the result, bounding the TTL to both the short max window and the
        # token's remaining lifetime so we never serve a verification past exp.
        remaining = float(payload["exp"]) - time.time()
        ttl = min(_VERIFY_CACHE_MAX_TTL, remaining)
        if ttl > 0:
            self._verify_cache[cache_key] = (user, now + ttl)
            self._verify_cache.move_to_end(cache_key)
            # Bound memory: evict the least-recently-used entries once the cache
            # exceeds its cap. Entries are short-lived anyway; this only matters
            # under a burst of distinct valid tokens within the TTL window.
            while len(self._verify_cache) > _VERIFY_CACHE_MAX_ENTRIES:
                self._verify_cache.popitem(last=False)

        return user
