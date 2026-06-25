"""
Tenant Middleware.

Extracts tenant information from the authenticated user and sets the tenant context.
"""

from starlette.types import ASGIApp, Receive, Scope, Send

from core.auth import AuthUser
from core.context import (
    reset_tenant_context,
    reset_user_context,
    set_tenant_context,
    set_user_context,
)

try:
    import structlog  # type: ignore
    from structlog.contextvars import bind_contextvars  # type: ignore
except ImportError:
    structlog = None  # type: ignore
    bind_contextvars = None  # type: ignore


class TenantMiddleware:
    """Pure ASGI middleware that derives tenant context from the auth user.

    Reads ``scope['user']`` (set by upstream auth middleware/dependencies) and
    binds the tenant id to a contextvar plus structlog. Skips lifespan and
    websocket scopes.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Auth populates user via either Starlette's ``scope['user']`` or the
        # ``request.state.user`` wrapper (backed by ``scope['state']``).
        state = scope.get("state") or {}
        user = (
            state.get("user")
            if isinstance(state, dict)
            else getattr(state, "user", None)
        )
        if not isinstance(user, AuthUser):
            scope_user = scope.get("user")
            user = scope_user if isinstance(scope_user, AuthUser) else None

        tenant_id = user.tenant_id if isinstance(user, AuthUser) else "default"
        user_id = user.user_id if isinstance(user, AuthUser) else None

        token = set_tenant_context(tenant_id)
        user_token = set_user_context(user_id) if user_id else None
        if structlog and bind_contextvars is not None:
            bind_contextvars(tenant_id=tenant_id)

        try:
            await self.app(scope, receive, send)
        finally:
            reset_tenant_context(token)
            if user_token is not None:
                reset_user_context(user_token)
