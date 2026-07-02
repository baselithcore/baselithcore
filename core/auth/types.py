"""
Authentication types and exceptions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


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
    roles: set[AuthRole] = field(default_factory=lambda: {AuthRole.USER})
    email: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    token_id: str | None = None
    expires_at: datetime | None = None
    # Explicit capability grants attached to this identity (a scoped API key or
    # a JWT "scopes" claim), on top of whatever the roles imply. Empty preserves
    # the pure role-based behaviour. See core.auth.scopes for the grammar.
    scopes: set[str] = field(default_factory=set)

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

    def effective_scopes(self) -> frozenset[str]:
        """All capabilities this identity holds (role-derived ∪ explicit).

        Imported lazily to avoid a circular import (``scopes`` depends on
        :class:`AuthRole` defined in this module).
        """
        from core.auth.scopes import effective_scopes

        return effective_scopes(self.roles, self.scopes)

    def has_scope(self, scope: str) -> bool:
        """Whether this identity is authorized for a single ``resource:action``.

        Honours the ``"*"`` and ``"resource:*"`` wildcards.
        """
        from core.auth.scopes import scope_satisfied

        return scope_satisfied(self.effective_scopes(), scope)

    def has_scopes(self, *scopes: str, require_all: bool = True) -> bool:
        """Whether this identity satisfies several scopes at once."""
        from core.auth.scopes import scopes_satisfied

        return scopes_satisfied(
            self.effective_scopes(), scopes, require_all=require_all
        )


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


class InsufficientScopeError(InsufficientPermissionsError):
    """Identity is authenticated but lacks a required capability scope.

    Subclasses :class:`InsufficientPermissionsError` so existing role-based
    handlers keep catching it, while scope-aware callers (and the API error
    envelope) can distinguish a missing capability from a missing role.
    """

    def __init__(self, message: str, *, required: set[str] | None = None) -> None:
        super().__init__(message)
        self.required = required or set()
