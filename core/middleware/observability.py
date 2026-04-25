"""
Observability Middleware.

Provides middleware for request ID tracking and logging context binding.
"""

import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.observability.logging import bind_context
from core.observability.setup import request_id_ctx


class RequestIdMiddleware:
    """Pure ASGI middleware that propagates ``X-Request-ID`` headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        incoming_id = ""
        for name, value in headers:
            if name == b"x-request-id":
                incoming_id = value.decode("latin-1")
                break
        request_id = incoming_id or str(uuid.uuid4())

        token = request_id_ctx.set(request_id)
        encoded_id = request_id.encode("latin-1")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers") or [])
                response_headers = [
                    (k, v) for k, v in response_headers if k != b"x-request-id"
                ]
                response_headers.append((b"x-request-id", encoded_id))
                message["headers"] = response_headers
            await send(message)

        try:
            with bind_context(request_id=request_id):
                await self.app(scope, receive, send_wrapper)
        finally:
            request_id_ctx.reset(token)
