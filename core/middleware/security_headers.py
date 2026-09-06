"""Pure ASGI security middlewares.

Houses the request-size limiter and the baseline security-header injector.
Both are implemented as pure ASGI middleware (no ``BaseHTTPMiddleware``) so
they never wrap requests in an extra anyio task and stay streaming-safe.

Both deliberately ignore ``websocket`` scopes: a handshake carries no HTTP
response to decorate and no ``http.request`` body to meter. Cross-origin
protection for WebSockets (CSWSH) is *not* missing as a result — it lives in
:class:`core.middleware.csrf.CSRFOriginMiddleware`, which validates the
handshake ``Origin`` against ``ALLOW_ORIGINS``.

Re-exported from :mod:`core.middleware.security` for backwards-compatible
imports.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.config import SecurityConfig, get_security_config
from core.middleware._security_metrics import SECURITY_EVENTS


class _BodyTooLarge(Exception):
    """Raised out of the receive channel once the streamed body crosses the cap.

    Propagates up through the application (router, ``ExceptionMiddleware``)
    back to :class:`RequestSizeLimitMiddleware`, which sits outside them and
    converts it into the 413. Never reaches ``ServerErrorMiddleware``.
    """


class RequestSizeLimitMiddleware:
    """Pure ASGI middleware enforcing a maximum request body size.

    Two-stage enforcement: first the ``Content-Length`` header (cheap reject),
    then a streaming byte counter on the receive channel (defends against
    chunked-encoding bypass and missing Content-Length).

    The streaming stage is a hard cut, not a post-hoc check: the chunk that
    crosses the cap is never handed to the application — the receive channel
    raises instead, so ``request.body()`` cannot materialise the rest of an
    oversized body before the 413 is written. A handler that swallows that
    exception and answers anyway still gets its response replaced by the 413.
    Both rejection paths send ``Connection: close`` so the server drops the
    connection instead of trying to drain the unread remainder for keep-alive.

    Configured via ``SecurityConfig.max_request_size_bytes``; set to 0 to
    disable. WebSocket and lifespan scopes are passed through unchanged.
    """

    def __init__(self, app: ASGIApp, max_bytes: int | None = None) -> None:
        self.app = app
        if max_bytes is None:
            max_bytes = get_security_config().max_request_size_bytes
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
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
        response_started = False
        rejected = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            if too_large:
                # A handler that caught the first cut-off and keeps reading
                # gets nothing further.
                raise _BodyTooLarge
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"") or b""
                received += len(body)
                if received > self.max_bytes:
                    too_large = True
                    SECURITY_EVENTS.labels(reason="request_too_large").inc()
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started, rejected
            if rejected:
                # Drop further frames from the downstream app after we
                # short-circuited the response.
                return
            if too_large and not response_started:
                rejected = True
                await self._reject(send)
                return
            response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            pass
        if too_large and not rejected and not response_started:
            rejected = True
            await self._reject(send)

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
    async def _reject(send: Send) -> None:
        body = b'{"detail":"Request body too large."}'
        await send(
            {
                "type": "http.response.start",
                # Literal: the constant was renamed across Starlette releases
                # (REQUEST_ENTITY_TOO_LARGE -> CONTENT_TOO_LARGE) and the
                # deprecated alias warns on every rejection.
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                    # The unread remainder of the body is never drained:
                    # tell the server (and client) to drop the connection
                    # rather than keep it alive on a half-read request.
                    (b"connection", b"close"),
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
        """Return a strict default CSP for runtime responses.

        ``connect-src`` deliberately has no bare ``ws:``/``wss:`` sources: a
        scheme-only source matches EVERY host, handing an XSS foothold a free
        WebSocket exfiltration channel. CSP3 browsers already allow same-origin
        ws/wss under ``'self'``; deployments that need cross-origin sockets set
        ``CONTENT_SECURITY_POLICY`` explicitly (operator value always wins).

        ``img-src`` carries ``blob:`` because a plugin SPA that fetches an
        image over the authenticated API can only render it through
        ``URL.createObjectURL`` — the bytes never come back as a URL the
        browser could load directly. A ``blob:`` URL is minted by the page
        itself from data it already holds, so it opens no new exfiltration
        path the way a scheme-only host source would.

        ``base-uri``, ``form-action`` and ``object-src`` default to
        *permissive* when omitted, so they must be stated: without them a
        ``<base>`` injection rebases every relative script URL, an injected
        ``<form action>`` exfiltrates credentials to a foreign origin, and the
        legacy plugin-embedding vector stays open.
        """
        return (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "object-src 'none'; "
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
            "img-src 'self' data: blob: https:; "
            "font-src 'self' data:; "
            "worker-src 'self' blob:; "
            "connect-src 'self'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "object-src 'none'; "
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
        cached: list[tuple[bytes, bytes]] | None = getattr(self, cache_attr)
        if cached is not None:
            return cached
        headers: list[tuple[bytes, bytes]] = [
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", self.config.frame_options.encode("latin-1")),
            (b"referrer-policy", b"same-origin"),
            # The legacy XSS auditor header is deprecated; modern browsers ignore
            # it and "1; mode=block" could itself introduce a side channel in old
            # ones. OWASP now recommends disabling it and relying on the CSP.
            (b"x-xss-protection", b"0"),
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
            # Cross-origin isolation pair (OWASP Secure Headers Project). Read
            # with ``getattr``/``isinstance`` so a partial config double (tests,
            # legacy stubs) omitting the fields simply emits neither header.
            coop = getattr(self.config, "cross_origin_opener_policy", None)
            if isinstance(coop, str) and coop:
                headers.append((b"cross-origin-opener-policy", coop.encode("latin-1")))
            corp = getattr(self.config, "cross_origin_resource_policy", None)
            if isinstance(corp, str) and corp:
                headers.append(
                    (b"cross-origin-resource-policy", corp.encode("latin-1"))
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

    @staticmethod
    def _carries_credential(headers: list[tuple[bytes, bytes]]) -> bool:
        """True when the request authenticates via ``Authorization``/``X-API-Key``."""
        for name, value in headers:
            if name in (b"authorization", b"x-api-key") and value.strip():
                return True
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        baseline = self._build_headers(docs=self._is_docs_path(scope.get("path", "")))
        credentialed = self._carries_credential(scope.get("headers") or [])

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers") or [])
                existing = {k for k, _ in response_headers}
                for k, v in baseline:
                    if k not in existing:
                        response_headers.append((k, v))
                # A response earned with a credential is for that caller only:
                # keep it out of every cache (browser, proxy, CDN) unless the
                # route set its own directive (OWASP REST Security: no-store on
                # authenticated responses). Anonymous responses are untouched.
                if credentialed and b"cache-control" not in existing:
                    response_headers.append((b"cache-control", b"no-store"))
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


__all__ = ["RequestSizeLimitMiddleware", "SecurityHeadersMiddleware"]
