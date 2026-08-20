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
    # Pure capability identity: grants NOTHING on its own; authorization comes
    # solely from the explicit scopes attached to it. Never promoted to another
    # role, so a scoped key can only reach routes whose required capability its
    # scopes cover. Used for least-privilege scoped API keys.
    SCOPED = "scoped"


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

    @property
    def is_agent_delegated(self) -> bool:
        """Whether this identity is an RFC 8693 agent delegation.

        Discriminated by ``act.client_id`` (the token-exchange path always
        mints it — see ``core.auth.oauth.build_actor_claim``). Admin
        impersonation also carries ``act`` but with only ``act.sub``, and is
        deliberately NOT treated as an agent delegation: the admin must see
        exactly what the target sees.
        """
        act = self.metadata.get("act")
        return isinstance(act, dict) and bool(act.get("client_id"))

    def effective_scopes(self) -> frozenset[str]:
        """All capabilities this identity holds.

        Role-derived ∪ explicit for a first-party identity. For an **agent-
        delegated** identity (RFC 8693 ``act`` with ``client_id``) the explicit
        scopes alone: the exchange intersected them down ("scope only
        narrows"), and re-expanding the user's roles here would union the
        narrowing away — ``roles:["admin"]`` would hand the agent ``"*"``.

        Imported lazily to avoid a circular import (``scopes`` depends on
        :class:`AuthRole` defined in this module).
        """
        from core.auth.scopes import effective_scopes

        if self.is_agent_delegated:
            return frozenset(self.scopes)
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
