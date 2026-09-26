"""
Cross-origin perimeter guard for HTTP *and* WebSocket (pure ASGI).

Two distinct attacks share one allowlist and one decision function here:

**CSRF (HTTP).** The main API uses Bearer / API-key auth (not browser
cookies), so CSRF only matters for the admin endpoints that rely on HTTP
Basic Auth. Browsers automatically include Basic Auth credentials on
same-origin requests; rejecting cross-origin state-changing requests
without an allowed ``Origin`` prevents CSRF on those endpoints.

**CSWSH (WebSocket).** The Same-Origin Policy does *not* apply to
WebSockets: any page on the internet may open
``new WebSocket("wss://your-host/...")`` and the browser attaches the
ambient cookies/Basic-Auth credentials to the handshake. Nothing but an
``Origin`` check on the handshake stands between a malicious page and an
authenticated socket, so the handshake is validated against the very same
allowlist before it ever reaches the route (see :meth:`_reject_websocket`
for how the denial is emitted at the ASGI level).

Requests without an ``Origin`` header (direct curl calls, SDKs, server-to-
server jobs, non-browser WebSocket clients) are still passed through — they
cannot be forged by a malicious page. The one exception is the
``Sec-Fetch-Site`` fallback documented on :meth:`_rejection_reason`.

Same-origin browser requests — a page served by this deployment calling its
own API, e.g. the admin dashboard at ``/admin`` — are always admitted, with
no ``ALLOW_ORIGINS`` entry needed: they cannot be a cross-site forgery.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.middleware._security_metrics import SECURITY_EVENTS
from core.observability.logging import get_logger

logger = get_logger(__name__)

_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})

# RFC 6455 policy-violation close code, used when the transport cannot carry
# a real HTTP denial response.
_WS_POLICY_VIOLATION = 1008

# ASGI scope scheme -> the web-origin scheme a browser would send in
# ``Origin`` for the same endpoint (a WebSocket handshake carries the page's
# http(s) origin, not ws(s)).
_ORIGIN_SCHEME = {"http": "http", "https": "https", "ws": "http", "wss": "https"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin_tuple(scheme: str, netloc: str) -> tuple[str, str, int] | None:
    """Normalise ``scheme`` + ``host[:port]`` to a comparable origin tuple.

    Returns:
        ``(scheme, lowercase host, explicit-or-default port)``, or ``None``
        when the pieces do not form a valid http(s) origin.
    """
    scheme = scheme.lower()
    if scheme not in _DEFAULT_PORTS or not netloc:
        return None
    try:
        parts = urlsplit(f"{scheme}://{netloc}")
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname
    if not host or parts.username is not None or parts.path not in ("", "/"):
        return None
    return scheme, host, port if port is not None else _DEFAULT_PORTS[scheme]


def _is_own_origin(origin: str, scope: Scope, host_header: str | None) -> bool:
    """Whether ``origin`` names this very endpoint (scheme, host and port).

    The request's own origin is rebuilt from the ASGI scheme and the ``Host``
    header. ``Host`` is attacker-controlled only for non-browser clients,
    which are not a CSRF vector; a browser always sends the target's host, so
    a cross-site page can never make its ``Origin`` equal it. (DNS rebinding,
    where the two do match, yields no ambient credentials — the browser
    scopes them to the rebinding origin — and is ``TrustedHost``'s job.)
    """
    if host_header is None:
        return False
    request_scheme = _ORIGIN_SCHEME.get(str(scope.get("scheme", "")).lower())
    if request_scheme is None:
        return False
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    own = _origin_tuple(request_scheme, host_header)
    claimed = _origin_tuple(parts.scheme, parts.netloc)
    return own is not None and own == claimed


class CSRFOriginMiddleware:
    """Validate ``Origin`` on state-changing requests and WS handshakes."""

    def __init__(self, app: ASGIApp, allow_origins: list[str]) -> None:
        self.app = app
        self.allow_origins = frozenset(allow_origins)
        self.wildcard = "*" in self.allow_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope["type"]

        if scope_type == "websocket":
            # Every handshake is checked — a WebSocket has no "safe method"
            # equivalent: the socket is bidirectional from the first frame.
            reason = self._rejection_reason(scope)
            if reason is not None:
                await self._reject_websocket(scope, receive, send, reason)
                return
            await self.app(scope, receive, send)
            return

        if scope_type != "http" or scope["method"] not in _STATE_CHANGING_METHODS:
            await self.app(scope, receive, send)
            return

        reason = self._rejection_reason(scope)
        if reason is not None:
            SECURITY_EVENTS.labels(reason="csrf_origin_rejected").inc()
            response = JSONResponse(
                status_code=403,
                content={"detail": "CSRF check failed: origin not allowed."},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------
    def _rejection_reason(self, scope: Scope) -> str | None:
        """Return a human-readable rejection reason, or ``None`` to allow.

        Two signals are consulted, in order:

        1. ``Origin`` — the primary check. Present and not in the allowlist
           ⇒ reject (unless the allowlist is the ``*`` wildcard, which is an
           explicit operator decision to accept every origin), **except** for
           a same-origin request: ``Sec-Fetch-Site: same-origin`` (set by the
           user agent, unforgeable from script) or an ``Origin`` whose
           scheme, host and port equal the request's own (the fallback for
           user agents that predate Fetch metadata). A page served by this
           deployment is not a forgery, so it needs no allowlist entry.
        2. ``Sec-Fetch-Site`` — the fallback for the case ``Origin`` cannot
           cover. Historically an absent ``Origin`` was treated as "not a
           browser, therefore not forgeable", but that is only *usually*
           true: some cross-site navigations/form posts and origin-stripping
           intermediaries reach the server without one, and under the ``*``
           wildcard the ``Origin`` branch is a no-op anyway. Every browser
           still in support sends ``Sec-Fetch-Site`` (it is added by the UA
           and is unforgeable from script), so ``cross-site`` is a positive
           statement that a *browser* initiated this from another site —
           rejected even in wildcard mode.

        Deliberately **not** rejected:

        * ``Sec-Fetch-Site: same-site`` *without* an ``Origin`` — a sibling
           host under the same registrable domain. With an ``Origin`` present
           a sibling host is **not** implicitly trusted (``same-site`` also
           covers sibling subdomains the operator may not control, e.g.
           user-content hosts): it must be listed in ``ALLOW_ORIGINS``.
        * *Both headers absent* — the non-browser case (curl, server-to-
           server SDKs, native WebSocket clients). A browser cannot produce
           that combination, so keeping it allowed closes no hole while
           preserving every programmatic client.
        """
        origin: str | None = None
        sec_fetch_site: str | None = None
        host: str | None = None
        for name, value in scope.get("headers") or []:
            lowered = name.lower()
            if lowered == b"origin":
                origin = value.decode("latin-1")
            elif lowered == b"sec-fetch-site":
                sec_fetch_site = value.decode("latin-1").strip().lower()
            elif lowered == b"host":
                host = value.decode("latin-1").strip()

        if origin is None:
            if sec_fetch_site == "cross-site":
                logger.warning(
                    "Cross-origin request rejected: no Origin header but "
                    "Sec-Fetch-Site=cross-site (browser-initiated from another site)"
                )
                return "cross-site request without an Origin header"
            return None

        if self.wildcard or origin in self.allow_origins:
            return None

        if sec_fetch_site == "same-origin" or _is_own_origin(origin, scope, host):
            return None

        # Name the rejected origin and the configured allowlist. This is the
        # exact failure that bites when the app is moved behind a reverse
        # proxy: the browser now sends the public Origin (e.g.
        # https://api.example.com) which is absent from ALLOW_ORIGINS, so
        # every login/refresh POST 403s. An opaque 403 makes that a
        # multi-hour hunt; logging the mismatch makes the fix obvious (add
        # the origin to ALLOW_ORIGINS).
        logger.warning(
            "Cross-origin request rejected: %s not in ALLOW_ORIGINS %s "
            "(add the public/proxied origin to ALLOW_ORIGINS)",
            origin,
            sorted(self.allow_origins),
        )
        return "origin not allowed"

    # ------------------------------------------------------------------
    # WebSocket denial
    # ------------------------------------------------------------------
    async def _reject_websocket(
        self, scope: Scope, receive: Receive, send: Send, reason: str
    ) -> None:
        """Deny a WebSocket handshake without leaving the client hanging.

        A bare ``return`` would drop the connection on the floor: the server
        never answers the handshake and the peer waits for a timeout. The
        ASGI protocol offers two correct denials, and which one is available
        depends on the server:

        * **Denial response** (``websocket.http.response.*``) — the ASGI
          "WebSocket Denial Response" extension, advertised in
          ``scope["extensions"]`` by uvicorn (both the ``websockets`` and
          ``wsproto`` implementations) and by Starlette's ``TestClient``.
          The handshake is answered with a real HTTP ``403`` plus a body,
          which is what an operator sees in the browser devtools and in
          access logs — by far the most debuggable outcome.
        * **Pre-accept close** (``websocket.close``) — the universal
          fallback. Sent before ``websocket.accept``, every ASGI server
          turns it into a failed handshake (uvicorn answers HTTP 403).

        The initial ``websocket.connect`` is consumed first so the exchange
        stays a well-formed ASGI conversation: the spec has the application
        receive ``websocket.connect`` before it may send anything, and
        Starlette's ``WebSocket`` state machine enforces exactly that.
        """
        SECURITY_EVENTS.labels(reason="cswsh_handshake_rejected").inc()
        logger.warning(
            "WebSocket handshake rejected (%s): path=%s", reason, scope.get("path")
        )

        message: Message = await receive()
        if message.get("type") != "websocket.connect":  # pragma: no cover - defensive
            return

        extensions = scope.get("extensions") or {}
        if "websocket.http.response" in extensions:
            body = b'{"detail":"WebSocket handshake rejected: origin not allowed."}'
            await send(
                {
                    "type": "websocket.http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("latin-1")),
                    ],
                }
            )
            await send(
                {
                    "type": "websocket.http.response.body",
                    "body": body,
                    "more_body": False,
                }
            )
            return

        await send(
            {
                "type": "websocket.close",
                "code": _WS_POLICY_VIOLATION,
                "reason": "origin not allowed",
            }
        )


__all__ = ["CSRFOriginMiddleware"]
