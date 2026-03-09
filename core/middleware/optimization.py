"""
Optimization Middleware.

Provides middleware for static asset caching and smart Gzip compression.
"""

from typing import Optional
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


class StaticCacheMiddleware(BaseHTTPMiddleware):
    """Adds Cache-Control for static/console assets. Passes WebSocket/lifespan scopes through unchanged."""

    def __init__(self, app, max_age: int = 86400):
        super().__init__(app)
        self.max_age = max_age

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    async def dispatch(self, request, call_next):
        """
        Intersects the request to apply caching headers based on the path.

        Args:
            request: The incoming Starlette/FastAPI request.
            call_next: The next handler in the middleware chain.

        Returns:
            The response with appropriate Cache-Control headers.
        """
        response = await call_next(request)
        path = request.url.path or ""
        content_type = (response.headers.get("content-type") or "").lower()

        if path.startswith("/static"):
            response.headers.setdefault(
                "cache-control", f"public, max-age={self.max_age}"
            )
        elif path.startswith("/console"):
            if "application/json" in content_type:
                # Evita di cache-are le API della console (es. lista KB)
                response.headers["cache-control"] = "no-store"
            else:
                response.headers.setdefault(
                    "cache-control", f"public, max-age={self.max_age}"
                )
        return response


class SmartGzipMiddleware(GZipMiddleware):
    """
    Applica compressione Gzip ECCETTO per i percorsi di streaming.
    Questo evita problemi di buffering che rompono l'effetto 'macchina da scrivere'.
    """

    def __init__(
        self,
        app: ASGIApp,
        minimum_size: int = 500,
        compresslevel: int = 9,
        excluded_paths: Optional[list[str]] = None,
    ):
        super().__init__(app, minimum_size=minimum_size, compresslevel=compresslevel)
        self.excluded_paths = excluded_paths or []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            for excluded in self.excluded_paths:
                if path.startswith(excluded):
                    # Skip Gzip logic completely for this request, pass to next app
                    await self.app(scope, receive, send)
                    return

        await super().__call__(scope, receive, send)
