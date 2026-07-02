"""Pure ASGI security middlewares.

Houses the request-size limiter and the baseline security-header injector.
Both are implemented as pure ASGI middleware (no ``BaseHTTPMiddleware``) so
they never wrap requests in an extra anyio task and stay streaming-safe.

Re-exported from :mod:`core.middleware.security` for backwards-compatible
imports.
"""

from __future__ import annotations

from fastapi import status
from starlette.types import ASGIApp

from core.config import SecurityConfig, get_security_config
from core.middleware._security_metrics import SECURITY_EVENTS


class RequestSizeLimitMiddleware:
    """Pure ASGI middleware enforcing a maximum request body size.

    Two-stage enforcement: first the ``Content-Length`` header (cheap reject),
    then a streaming byte counter on the receive channel (defends against
    chunked-encoding bypass and missing Content-Length).

    Configured via ``SecurityConfig.max_request_size_bytes``; set to 0 to
    disable. WebSocket and lifespan scopes are passed through unchanged.
    """

    def __init__(self, app: ASGIApp, max_bytes: int | None = None) -> None:
        self.app = app
        if max_bytes is None:
            max_bytes = get_security_config().max_request_size_bytes
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if self.max_bytes <= 0 or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Fast path: trust Content-Length when present.
        content_length = self._content_length(scope.get("headers") or [])
        if content_length is not None and content_length > self.max_bytes:
            SECURITY_EVENTS.labels(reason="request_too_large").inc()
            await self._reject(send)
            return

        received = 0
        too_large = False

        async def limited_receive():
            nonlocal received, too_large
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"") or b""
                received += len(body)
                if received > self.max_bytes:
                    too_large = True
            return message

        sent_response = False

        async def guarded_send(message):
            nonlocal sent_response
            if too_large and not sent_response:
                SECURITY_EVENTS.labels(reason="request_too_large").inc()
                await self._reject(send)
                sent_response = True
                return
            if sent_response:
                # Drop further frames from the downstream app after we
                # short-circuited the response.
                return
            await send(message)

        await self.app(scope, limited_receive, guarded_send)

    @staticmethod
    def _content_length(headers: list[tuple[bytes, bytes]]) -> int | None:
        for k, v in headers:
            if k.lower() == b"content-length":
                try:
                    return int(v.decode("latin-1"))
                except (ValueError, UnicodeDecodeError):
                    return None
        return None

    @staticmethod
    async def _reject(send) -> None:
        body = b'{"detail":"Request body too large."}'
        await send(
            {
                "type": "http.response.start",
                "status": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})


class SecurityHeadersMiddleware:
    """Pure ASGI middleware that injects baseline security headers.

    Re-implemented without ``BaseHTTPMiddleware`` to avoid the per-request
    anyio task wrapping. Header injection happens in the ``send`` wrapper so
    streaming responses are unaffected.
    """

    def __init__(self, app: ASGIApp, config: SecurityConfig | None = None) -> None:
        self.app = app
        self.config = config if config is not None else get_security_config()
        self._cached_headers: list[tuple[bytes, bytes]] | None = None
        self._cached_docs_headers: list[tuple[bytes, bytes]] | None = None

    def _default_csp(self) -> str:
        """Return a strict default CSP for runtime responses."""
        return (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none';"
        )

    def _docs_csp(self) -> str:
        """Relaxed CSP for Swagger UI / ReDoc pages.

        FastAPI's interactive docs load the Swagger/ReDoc bundles from the
        jsDelivr CDN and bootstrap them with an inline ``<script>``. The strict
        runtime CSP (``script-src 'self'``) blocks both, leaving a blank page.
        This policy whitelists the CDN and inline bootstrap for the docs routes
        only; every other response keeps the strict default.
        """
        return (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "worker-src 'self' blob:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )

    def _build_headers(self, *, docs: bool = False) -> list[tuple[bytes, bytes]]:
        """Pre-encode the static header list once per process.

        Args:
            docs: When True, emit the relaxed :meth:`_docs_csp` so the Swagger
                UI / ReDoc pages can load their CDN bundles. An operator-supplied
                ``content_security_policy`` always wins and is left untouched.
        """
        cache_attr = "_cached_docs_headers" if docs else "_cached_headers"
        cached = getattr(self, cache_attr)
        if cached is not None:
            return cached
        headers: list[tuple[bytes, bytes]] = [
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", self.config.frame_options.encode("latin-1")),
            (b"referrer-policy", b"same-origin"),
            (b"x-xss-protection", b"1; mode=block"),
        ]
        if self.config.security_headers_enabled:
            default_csp = self._docs_csp() if docs else self._default_csp()
            csp = (self.config.content_security_policy or default_csp).encode("latin-1")
            headers.append((b"content-security-policy", csp))
            if self.config.permissions_policy:
                headers.append(
                    (
                        b"permissions-policy",
                        self.config.permissions_policy.encode("latin-1"),
                    )
                )
            if self.config.enable_hsts:
                hsts = (
                    f"max-age={self.config.hsts_max_age}; includeSubDomains"
                ).encode("latin-1")
                headers.append((b"strict-transport-security", hsts))
        setattr(self, cache_attr, headers)
        return headers

    # Paths whose responses need the relaxed docs CSP (Swagger UI / ReDoc).
    _DOCS_PATHS = ("/docs", "/redoc")

    def _is_docs_path(self, path: str) -> bool:
        return any(path == p or path.startswith(p + "/") for p in self._DOCS_PATHS)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        baseline = self._build_headers(docs=self._is_docs_path(scope.get("path", "")))

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers") or [])
                existing = {k for k, _ in response_headers}
                for k, v in baseline:
                    if k not in existing:
                        response_headers.append((k, v))
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


__all__ = ["RequestSizeLimitMiddleware", "SecurityHeadersMiddleware"]
