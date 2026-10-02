"""Catch-all ``500`` rendered *inside* the observability layers (pure ASGI).

Starlette installs an ``Exception`` handler on ``ServerErrorMiddleware``, the
outermost layer of the stack — outside ``RequestIdMiddleware``,
``SecurityHeadersMiddleware`` and CORS. A problem document built there has
no ``X-Request-ID`` header, a ``null`` ``request_id`` member (the contextvar
was already reset on the way out), no CSP/nosniff, and no
``Access-Control-Allow-Origin`` — so a browser client sees an opaque CORS
failure instead of the 500, and the one correlation id an operator needs for
exactly these failures is missing.

This layer sits inside those three and renders the same RFC 9457 document
through :func:`core.api.errors.unhandled_exception_handler`, so the response
is stamped and correlated like every other. The exception is then
**re-raised**: ``ServerErrorMiddleware`` sees that a response has started,
skips its own rendering, and propagates it to the server exactly as before —
nothing is swallowed, server-side logging and test clients are unchanged.

A failure after the response has started cannot be answered at all; it is
propagated untouched.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class UnhandledErrorMiddleware:
    """Render an unhandled exception as ``problem+json`` before it propagates."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if response_started:
                raise
            # Local import: ``core.api.errors`` imports from this package.
            from core.api.errors import unhandled_exception_handler

            response = await unhandled_exception_handler(Request(scope, receive), exc)
            await response(scope, receive, send_wrapper)
            raise


__all__ = ["UnhandledErrorMiddleware"]
