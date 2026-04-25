"""
Optimization Middleware.

Provides middleware for static asset caching and smart Gzip compression.
"""

from typing import Optional

from fastapi.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class StaticCacheMiddleware:
    """Pure ASGI middleware that injects ``Cache-Control`` for static/console assets."""

    def __init__(self, app: ASGIApp, max_age: int = 86400) -> None:
        self.app = app
        self.max_age = max_age

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "") or ""
        is_static = path.startswith("/static")
        is_console = path.startswith("/console")
        if not (is_static or is_console):
            await self.app(scope, receive, send)
            return

        max_age_header = f"public, max-age={self.max_age}".encode("latin-1")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                content_type = b""
                cache_control_present = False
                for k, v in headers:
                    if k == b"content-type":
                        content_type = v.lower()
                    elif k == b"cache-control":
                        cache_control_present = True

                if is_console and b"application/json" in content_type:
                    headers = [(k, v) for k, v in headers if k != b"cache-control"]
                    headers.append((b"cache-control", b"no-store"))
                elif not cache_control_present:
                    headers.append((b"cache-control", max_age_header))

                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class SmartGzipMiddleware(GZipMiddleware):
    """
    Applica compressione Gzip ECCETTO per i percorsi di streaming.
    Questo evita problemi di buffering che rompono l'effetto 'macchina da scrivere'.
    """

    def __init__(
        self,
        app: ASGIApp,
        minimum_size: int = 500,
        compresslevel: int = 6,
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
