"""
Tenant Middleware.

Extracts tenant information from the authenticated user and sets the tenant context.
"""

from typing import Awaitable, Callable, Optional, Union

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from core.auth import AuthUser
from core.context import reset_tenant_context, set_tenant_context

try:
    import structlog  # type: ignore
    from structlog.contextvars import bind_contextvars  # type: ignore
except ImportError:
    structlog = None  # type: ignore
    bind_contextvars = None  # type: ignore


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware to set the tenant context based on the authenticated user.
    Exceptions related to missing tenant ID can be handled here if strict multi-tenancy is enforced.
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """
        Process the request, extracting tenant ID from the user instance attached to the request,
        if present.
        """
        # We assume that an AuthenticationMiddleware runs BEFORE this and sets request.scope["user"]
        # or request.state.user.
        # However, typically AuthUser is passed via dependencies in FastAPI.
        # If we are using a framework where auth happens in middleware, we might find it in request.user

        tenant_id = "default"

        user: Optional[Union[AuthUser, object]] = None

        # 1. Check request.state
        state_user = getattr(request.state, "user", None)
        if isinstance(state_user, AuthUser):
            user = state_user

        # 2. Check request.scope directly (standard Starlette AuthMiddleware sets it here)
        if not user:
            user = request.scope.get("user")
            if not isinstance(user, AuthUser):
                user = None

        # 3. Check request.scope directly
        if not user:
            scope = getattr(request, "scope", None)
            if isinstance(scope, dict) and "user" in scope:
                scope_user = scope.get("user")
                if isinstance(scope_user, AuthUser):
                    user = scope_user

        if user and isinstance(user, AuthUser):
            tenant_id = user.tenant_id

        # Set the context
        token = set_tenant_context(tenant_id)

        # Bind to structured logging if available
        if structlog and bind_contextvars is not None:
            bind_contextvars(tenant_id=tenant_id)

        try:
            response = await call_next(request)
            return response
        finally:
            if structlog:
                # We can't easily unbind just one variable in structlog without clearing all
                # or keeping track of previous state.
                # However, since this is a middleware at the boundary, clearing might be safe
                # OR we just rely on scope cleanup if using asyncio context management.
                # Ideally, we should use a token-like approach, but structlog binding is global to the contextvar.
                # bind_contextvars merges. clear_contextvars clears everything.
                # For safety in nested calls, we might want to just let it be,
                # but request scope usually ends here.
                pass
            reset_tenant_context(token)
