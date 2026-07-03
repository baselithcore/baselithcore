"""
API key validation and management.
"""

import hashlib
from datetime import UTC, datetime

from core.auth.types import AuthRole, AuthUser
from core.config.security import SecurityConfig, get_security_config
from core.observability.logging import get_logger

logger = get_logger(__name__)


class APIKeyValidator:
    """
    API key validation for service authentication.
    """

    def __init__(self, config: SecurityConfig | None = None) -> None:
        self._keys: dict[str, AuthUser] = {}
        self._config = config or get_security_config()
        self._load_from_config()

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
        # Least-privilege scoped keys: SERVICE role (no role-derived data-plane
        # access on its own beyond what the explicit scopes grant) + an explicit
        # capability set. Lets operators mint a key that can, e.g., only call
        # webhooks:write without handing out a broad role.
        for raw_key, scopes in self._config.api_keys_scoped.items():
            self.register_key(
                raw_key,
                "scoped-api",
                roles={AuthRole.SERVICE},
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
        # In the future this could be an async DB call
        hashed = self._hash_key(api_key)
        user = self._keys.get(hashed)
        if user:
            if user.expires_at and user.expires_at < datetime.now(UTC):
                return None
            return user
        return None

    async def revoke_key(self, api_key: str) -> bool:
        """Revoke an API key. Returns True if existed."""
        # In the future this could be an async DB call
        hashed = self._hash_key(api_key)
        if hashed in self._keys:
            del self._keys[hashed]
            return True
        return False

    def _hash_key(self, api_key: str) -> str:
        """Hash an API key for use as a lookup/index key.

        SHA-256 is deliberate here (not bcrypt/argon2): API keys are
        **high-entropy random tokens**, not human-chosen passwords, so they are
        not vulnerable to brute force or rainbow tables. A fast hash is required
        — this runs on every authenticated request — and a slow password KDF
        would add latency without any security benefit for random secrets.
        (CodeQL ``py/weak-sensitive-data-hashing`` is a false positive for
        token hashing; safe to dismiss.)
        """
        return hashlib.sha256(api_key.encode()).hexdigest()
