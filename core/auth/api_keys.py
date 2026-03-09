"""
API key validation and management.
"""

import hashlib
from datetime import datetime, timezone
from core.observability.logging import get_logger
from typing import Dict, Optional, Set

from core.auth.types import AuthRole, AuthUser
from core.config.security import SecurityConfig, get_security_config

logger = get_logger(__name__)


class APIKeyValidator:
    """
    API key validation for service authentication.
    """

    def __init__(self, config: Optional[SecurityConfig] = None) -> None:
        self._keys: Dict[str, AuthUser] = {}
        self._config = config or get_security_config()
        self._load_from_config()

    def _load_from_config(self) -> None:
        """Load keys from configuration."""
        for key in self._config.api_keys_user:
            self.register_key(key, "user-api", {AuthRole.USER})
        for key in self._config.api_keys_admin:
            self.register_key(key, "admin-api", {AuthRole.ADMIN, AuthRole.USER})
        for key in self._config.api_keys_job:
            self.register_key(key, "job-service", {AuthRole.SERVICE})

    def register_key(
        self,
        api_key: str,
        user_id: str,
        roles: Optional[Set[AuthRole]] = None,
        expires_at: Optional[datetime] = None,
    ) -> None:
        """Register an API key."""
        hashed = self._hash_key(api_key)
        self._keys[hashed] = AuthUser(
            user_id=user_id,
            roles=roles or {AuthRole.SERVICE},
            expires_at=expires_at,
        )

    async def validate_key(self, api_key: str) -> Optional[AuthUser]:
        """
        Validate an API key.

        Returns:
            AuthUser if valid, None otherwise
        """
        # In the future this could be an async DB call
        hashed = self._hash_key(api_key)
        user = self._keys.get(hashed)
        if user:
            if user.expires_at and user.expires_at < datetime.now(timezone.utc):
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
        """Hash API key for storage."""
        return hashlib.sha256(api_key.encode()).hexdigest()
