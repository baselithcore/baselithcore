"""
Observability Middleware.

Provides middleware for request ID tracking and logging context binding.
"""

import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import Receive, Scope, Send
from core.observability.setup import request_id_ctx
from core.observability.logging import bind_context


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Adds X-Request-ID to every HTTP response. Passes WebSocket/lifespan scopes through unchanged."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only process HTTP requests; let WebSocket and lifespan pass through untouched.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    async def dispatch(self, request, call_next):
        """
        Assigns a unique request ID to the current context and response.

        Args:
            request: The incoming Starlette/FastAPI request.
            call_next: The next handler in the middleware chain.

        Returns:
            The response with the X-Request-ID header.
        """
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = request_id_ctx.set(request_id)

        with bind_context(request_id=request_id):
            try:
                response = await call_next(request)
            finally:
                request_id_ctx.reset(token)

        response.headers["x-request-id"] = request_id
        return response
