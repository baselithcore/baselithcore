"""
Optimization Middleware.

Provides middleware for static asset caching and smart Gzip compression.
"""

from fastapi.middleware.gzip import GZipMiddleware
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipResponder
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Response content-types that are streamed incrementally (token-by-token
#: NDJSON, Server-Sent Events, JSON streams). Gzip-buffering any of these
#: collapses the live stream into a single delayed flush — the "typewriter"
#: effect dies. Detected from the *response* content-type, so it covers
#: streaming endpoints regardless of the request's ``Accept`` header (a
#: ``fetch``-based NDJSON reader sends no ``text/event-stream`` Accept).
_STREAMING_CONTENT_TYPES: tuple[bytes, ...] = (
    b"text/event-stream",
    b"application/x-ndjson",
    b"application/stream+json",
    b"application/jsonl",
    b"application/x-jsonlines",
)


def _is_streaming_response(headers: Headers) -> bool:
    """True when a response must not be gzip-buffered (live stream)."""
    content_type = (
        headers.get("content-type", "").split(";", 1)[0].strip().encode("latin-1")
    )
    if content_type in _STREAMING_CONTENT_TYPES:
        return True
    # Explicit opt-out set by streaming endpoints (also disables proxy
    # buffering). If a handler declares it, honour it as a hard no-gzip signal.
    return headers.get("x-accel-buffering", "").strip().lower() == "no"


class _StreamAwareGZipResponder(GZipResponder):
    """GZip responder that passes streaming responses through uncompressed.

    Starlette's responder already forwards responses that declare a
    ``Content-Encoding`` untouched (``content_encoding_set``). We piggyback on
    that passthrough path for streaming media types / ``X-Accel-Buffering: no``:
    the decision is made from the buffered ``http.response.start`` headers, so
    no body is ever accumulated for a stream.
    """

    async def send_with_gzip(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = Headers(raw=message.get("headers") or [])
            if _is_streaming_response(headers):
                self.initial_message = message
                self.content_encoding_set = True  # reuse parent's raw passthrough
                return
        await super().send_with_gzip(message)


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
        excluded_paths: list[str] | None = None,
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

            # Bypass compression for Server-Sent Events regardless of path:
            # buffering an SSE response breaks the live stream (frames never
            # flush). EventSource always sends ``Accept: text/event-stream``,
            # so this covers every SSE endpoint without hardcoding paths.
            if self._accepts_event_stream(scope):
                await self.app(scope, receive, send)
                return

            # Client accepts gzip: route through a stream-aware responder that
            # skips compression for streaming *responses* (NDJSON token stream,
            # SSE, X-Accel-Buffering: no) detected from the response headers —
            # a fetch-based NDJSON reader sends no event-stream Accept, so the
            # request-time check above can't catch it.
            headers = Headers(scope=scope)
            if "gzip" in headers.get("Accept-Encoding", ""):
                responder = _StreamAwareGZipResponder(
                    self.app, self.minimum_size, compresslevel=self.compresslevel
                )
                await responder(scope, receive, send)
                return

        await super().__call__(scope, receive, send)

    @staticmethod
    def _accepts_event_stream(scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"accept" and b"text/event-stream" in value.lower():
                return True
        return False
