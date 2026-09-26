"""
API key validation and management.

Keys are loaded from env config into process memory. When the deployment
declares a Redis cache backend (``CACHE_BACKEND=redis``), revocation is backed
by a persistent Redis denylist so it propagates across workers/replicas and
survives restarts (config-sourced keys would otherwise reload on boot);
otherwise revocation is process-local.
"""

from datetime import UTC, datetime
from typing import Any

from core.auth.types import AuthRole, AuthUser
from core.config.security import SecurityConfig, get_security_config
from core.observability.logging import get_logger
from core.security.digest import credential_digest

logger = get_logger(__name__)


def _redis_denylist_declared() -> bool:
    """Whether the deployment declared Redis as its shared cache backend.

    Same rule as the rate limiter's fail-mode resolution: ``CACHE_BACKEND``
    other than ``redis`` is a single-node deployment choice, where
    process-local revocation is the design rather than a degraded state.
    Merely *constructing* a Redis client proves nothing — the client is lazy
    and connects on first use — so the declaration is what decides whether a
    denylist read failure is an outage (fail mode applies) or the absence of
    a backend (no denylist at all).
    """
    try:
        from core.config import get_storage_config

        return getattr(get_storage_config(), "cache_backend", "") == "redis"
    except Exception:  # pragma: no cover  # silent-ok: no config = undeclared
        return False


class APIKeyValidator:
    """
    API key validation for service authentication.

    Revocation semantics: ``revoke_key`` removes the key locally AND writes a
    persistent tombstone to Redis, so every worker/replica sharing the cache
    rejects the key and a restart cannot resurrect it. When no Redis cache
    backend is declared (``CACHE_BACKEND`` is not ``redis``) the validator
    uses process-local revocation (single-node behavior). With Redis declared,
    denylist *read* failures resolve per ``API_KEY_REVOCATION_FAIL_MODE``:
    ``closed`` (the default) rejects the key, ``open`` accepts it on local
    state — see :meth:`_is_denylisted`.
    """

    def __init__(self, config: SecurityConfig | None = None) -> None:
        self._keys: dict[str, AuthUser] = {}
        self._config = config or get_security_config()
        self._redis: Any | None = None
        self._denylist_prefix = "auth:api_key_denylist:"
        self._denylist_declared = _redis_denylist_declared()
        if self._denylist_declared:
            self._connect_denylist()
        else:
            logger.info(
                "api_key_denylist_process_local",
                reason="CACHE_BACKEND is not redis",
            )
        self._load_from_config()

    def _connect_denylist(self) -> None:
        """Bind the shared Redis denylist (only when Redis is declared)."""
        try:
            from core.cache.redis_cache import create_redis_client
            from core.config.cache import get_redis_cache_config

            cache_config = get_redis_cache_config()
            self._redis = create_redis_client(cache_config.url)
            self._denylist_prefix = cache_config.cache_prefix + ":api_key_denylist:"
        except Exception as exc:  # pragma: no cover - env-dependent
            logger.warning(
                "api_key_denylist_redis_unavailable",
                error=str(exc),
                note="denylist reads resolve per API_KEY_REVOCATION_FAIL_MODE",
            )

    def _load_from_config(self) -> None:
        """Load keys from configuration."""
        for key in self._config.api_keys_user:
            self.register_key(key.get_secret_value(), "user-api", {AuthRole.USER})
        for key in self._config.api_keys_admin:
            self.register_key(
                key.get_secret_value(), "admin-api", {AuthRole.ADMIN, AuthRole.USER}
            )
        for key in self._config.api_keys_job:
            self.register_key(key.get_secret_value(), "job-service", {AuthRole.SERVICE})
        # Least-privilege scoped keys: the SCOPED role grants NO role-derived
        # scopes and is never promoted to job/service, so the key's access is
        # exactly its explicit capability set — nothing more. Lets operators
        # mint a key that can, e.g., only call webhooks:write without it also
        # inheriting the broad SERVICE data-plane or reaching control-plane
        # (admin/job) routes.
        for secret_key, scopes in self._config.api_keys_scoped.items():
            self.register_key(
                secret_key.get_secret_value(),
                "scoped-api",
                roles={AuthRole.SCOPED},
                scopes=set(scopes),
            )

    def register_key(
        self,
        api_key: str,
        user_id: str,
        roles: set[AuthRole] | None = None,
        expires_at: datetime | None = None,
        scopes: set[str] | None = None,
    ) -> None:
        """Register an API key, optionally with explicit capability scopes."""
        hashed = self._hash_key(api_key)
        self._keys[hashed] = AuthUser(
            user_id=user_id,
            roles=roles or {AuthRole.SERVICE},
            expires_at=expires_at,
            scopes=scopes or set(),
        )

    async def validate_key(self, api_key: str) -> AuthUser | None:
        """
        Validate an API key.

        Returns:
            AuthUser if valid, None otherwise
        """
        hashed = self._hash_key(api_key)
        user = self._keys.get(hashed)
        if not user:
            return None
        if user.expires_at and user.expires_at < datetime.now(UTC):
            return None
        if await self._is_denylisted(hashed):
            return None
        return user

    async def revoke_key(self, api_key: str) -> bool:
        """Revoke an API key. Returns True if existed.

        Writes a persistent Redis tombstone (no TTL: config-sourced keys are
        long-lived and reload on every boot) so the revocation reaches all
        workers/replicas and survives restarts.
        """
        hashed = self._hash_key(api_key)
        existed = hashed in self._keys
        self._keys.pop(hashed, None)
        if self._redis is not None:
            try:
                # Tombstone even unknown keys: another worker may hold them.
                await self._redis.set(self._denylist_prefix + hashed, b"1")
            except Exception as exc:
                logger.error(
                    "api_key_denylist_write_failed",
                    error=str(exc),
                    note="revocation is process-local until Redis recovers",
                )
        return existed

    async def reinstate_key(self, api_key: str) -> None:
        """Clear a key's revocation tombstone (deliberate operator action).

        Revocation is persistent by design — a restart or re-registration must
        not silently resurrect a revoked key. Re-trusting the same key value
        therefore requires this explicit call in addition to registering it.
        """
        hashed = self._hash_key(api_key)
        if self._redis is not None:
            try:
                await self._redis.delete(self._denylist_prefix + hashed)
            except Exception as exc:
                logger.error("api_key_denylist_clear_failed", error=str(exc))

    async def _is_denylisted(self, hashed: str) -> bool:
        """Check the shared denylist, honouring the configured fail mode.

        A read failure is ambiguous: the key may or may not be revoked. Which
        way that ambiguity resolves is a policy decision, so it is a setting
        (``API_KEY_REVOCATION_FAIL_MODE``) rather than a hard-coded default:

        * ``closed`` (the default) treats the key as revoked. The caller sees
          the same rejection it already handles for an unknown key, so a
          revocation performed on another worker survives a cache outage.
        * ``open`` trusts process-local state, which is what this method used
          to do unconditionally — convenient, but it means anyone able to make
          Redis unreachable un-revokes every key revoked elsewhere.

        No denylist backend at all (no Redis cache backend declared) is not an outage but a deployment choice —
        single-node, process-local revocation — and is unaffected by the fail
        mode.

        Args:
            hashed: The digest of the presented key.

        Returns:
            True when the key must be rejected.
        """
        if self._redis is None and not self._denylist_declared:
            return False
        try:
            if self._redis is None:
                # Declared but the client could not even be built: an outage.
                raise ConnectionError("Redis denylist client unavailable")
            return bool(await self._redis.exists(self._denylist_prefix + hashed))
        except Exception as exc:
            if self._config.api_key_revocation_fail_mode == "open":
                logger.warning(
                    "api_key_denylist_read_failed",
                    error=str(exc),
                    fail_mode="open",
                    note="key accepted on process-local state",
                )
                return False
            logger.error(
                "api_key_denylist_read_failed",
                error=str(exc),
                fail_mode="closed",
                note="key rejected: revocation state is unknown",
            )
            return True

    def _hash_key(self, api_key: str) -> str:
        """Hash an API key for use as a lookup/index key.

        SHA-256 is deliberate here (not bcrypt/argon2): API keys are
        **high-entropy random tokens**, not human-chosen passwords, so they are
        not vulnerable to brute force or rainbow tables. A fast hash is required
        — this runs on every authenticated request — and a slow password KDF
        would add latency without any security benefit for random secrets.
        The premise is enforced at the config boundary: ``SecurityConfig``
        warns about any configured key shorter than 32 characters, so a
        hand-typed (password-like) key does not silently get token treatment.

        The digest itself comes from :func:`core.security.digest.credential_digest`,
        shared with the JWT verify cache and the rate limiter so the decision is
        recorded (and reviewed) in exactly one place.
        """
        return credential_digest(api_key.encode())
