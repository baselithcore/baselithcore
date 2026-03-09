"""
Authentication types and exceptions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Set


class AuthRole(str, Enum):
    """User roles for authorization."""

    ANONYMOUS = "anonymous"
    USER = "user"
    ADMIN = "admin"
    SERVICE = "service"  # For service-to-service auth
    GUEST = "guest"  # Read-only access to dashboards
    JOB = "job"  # Automated job/scheduler access


@dataclass
class AuthUser:
    """Authenticated user context."""

    user_id: str
    tenant_id: str = "default"
    roles: Set[AuthRole] = field(default_factory=lambda: {AuthRole.USER})
    email: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    token_id: Optional[str] = None
    expires_at: Optional[datetime] = None

    def has_role(self, role: AuthRole) -> bool:
        """Check if user has a specific role."""
        return role in self.roles

    def is_admin(self) -> bool:
        """Check if user is admin."""
        return AuthRole.ADMIN in self.roles

    @property
    def is_authenticated(self) -> bool:
        """Check if user is authenticated (not anonymous)."""
        return AuthRole.ANONYMOUS not in self.roles


class AuthError(Exception):
    """Base authentication error."""

    pass


class TokenExpiredError(AuthError):
    """Token has expired."""

    pass


class InvalidTokenError(AuthError):
    """Token is invalid."""

    pass


class InsufficientPermissionsError(AuthError):
    """User lacks required permissions."""

    pass
