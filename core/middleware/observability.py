"""
Observability Middleware.

Provides middleware for request ID tracking and logging context binding.
"""

import re
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.observability.logging import bind_context
from core.observability.setup import request_id_ctx

# Accepted shape for a caller-supplied ``X-Request-ID``. Correlation ids in the
# wild are UUIDs, ULIDs, trace ids or short opaque tokens — a bounded ASCII
# token covers all of them. Anything else (CR/LF, whitespace, ``;``, non-ASCII,
# an 8 KiB blob) is replaced by a fresh UUID: the incoming value is echoed on
# the response, bound into every log line for the request and copied into the
# RFC 9457 ``request_id`` member, so an unvalidated header is a log-injection /
# header-injection primitive and an unbounded per-request allocation.
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")


class RequestIdMiddleware:
    """Pure ASGI middleware that propagates ``X-Request-ID`` headers.

    An incoming id is honoured only when it matches ``_REQUEST_ID_RE``;
    otherwise (missing or unsafe) a UUID4 is generated. Either way the id is
    bound to ``request_id_ctx`` + the structlog context and echoed back.
    """

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
        if _REQUEST_ID_RE.fullmatch(incoming_id):
            request_id = incoming_id
        else:
            request_id = str(uuid.uuid4())

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
            with bind_context(
                request_id=request_id,
                http_path=scope.get("path", ""),
                http_method=scope.get("method", ""),
            ):
                await self.app(scope, receive, send_wrapper)
        finally:
            request_id_ctx.reset(token)
