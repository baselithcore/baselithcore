"""Capability gate for routes that the coarse role gate alone would over-admit.

``require_user`` admits ``scoped`` identities — least-privilege API keys and
narrowed delegation tokens — so that capability-enforced routes can adjudicate
them by their explicit scopes. A route that stops at ``require_user`` therefore
hands such a credential the whole surface: a key minted for ``webhooks:read``
could drive the agent loop, spend LLM budget and read RAG answers.

:func:`require_scope` closes that: the role gate runs first, then a ``scoped``
identity must hold the named capability. Role-based identities (user, job,
admin) and the anonymous development mode are adjudicated by the role gate
exactly as before.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, Request

from baselith.core.auth.manager import AuthManager
from baselith.core.middleware.security import require_user

SCOPED_ROLE = "scoped"


def enforce_scoped_capability(connection: Any, role: str, scope: str) -> None:
    """Require ``scope`` of a ``scoped`` identity; other roles pass through.

    Args:
        connection: The request or WebSocket whose ``state.user`` the auth gate
            populated.
        role: The role the auth gate matched for this connection.
        scope: The ``resource:action`` capability the route needs.

    Raises:
        InsufficientScopeError: A ``scoped`` identity does not hold ``scope``.
    """
    if role == SCOPED_ROLE:
        AuthManager.enforce_scopes(getattr(connection.state, "user", None), scope)


def require_scope(scope: str) -> Callable[[Request], Awaitable[str]]:
    """Build a dependency: the user gate, then ``scope`` for scoped identities.

    Args:
        scope: The ``resource:action`` capability the route needs.

    Returns:
        A FastAPI dependency returning the matched role.
    """

    # ``Depends(require_user)`` rather than a direct call: the role gate stays
    # one dependency, so FastAPI caches it per request and an application's
    # ``dependency_overrides[require_user]`` still governs these routes.
    async def _dependency(request: Request, role: str = Depends(require_user)) -> str:
        enforce_scoped_capability(request, role, scope)
        return role

    return _dependency


__all__ = ["SCOPED_ROLE", "enforce_scoped_capability", "require_scope"]
