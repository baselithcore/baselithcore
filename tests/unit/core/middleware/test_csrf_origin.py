"""Tests for the cross-origin perimeter guard (CSRF over HTTP, CSWSH over WS).

Two holes are covered here:

* a WebSocket handshake is never subject to the Same-Origin Policy, so it
  must be rejected at the ASGI level when ``Origin`` is not allowlisted;
* an HTTP request that omits ``Origin`` must still be rejected when
  ``Sec-Fetch-Site: cross-site`` proves a browser initiated it from another
  site — including in ``ALLOW_ORIGINS=*`` wildcard mode.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient, WebSocketDenialResponse
from starlette.websockets import WebSocket

from core.middleware.csrf import CSRFOriginMiddleware

ALLOWED = "https://app.example.com"
EVIL = "https://evil.example.com"


def _encode(headers: dict[str, str]) -> list[tuple[bytes, bytes]]:
    return [(k.encode(), v.encode()) for k, v in headers.items()]


async def _downstream_http(scope, receive, send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"ok", "more_body": False})


async def _run_http(
    middleware: CSRFOriginMiddleware,
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
) -> int:
    """Drive the middleware over an HTTP scope; return the response status."""
    scope = {
        "type": "http",
        "method": method,
        "path": "/admin/action",
        "headers": _encode(headers or {}),
    }
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await middleware(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return int(start["status"])


class _WSRecorder:
    """Downstream ASGI app that records whether the handshake reached it."""

    def __init__(self) -> None:
        self.reached = False

    async def __call__(self, scope, receive, send) -> None:
        self.reached = True
        await receive()
        await send({"type": "websocket.accept"})


async def _run_ws(
    middleware: CSRFOriginMiddleware,
    *,
    headers: dict[str, str] | None = None,
    denial_extension: bool = True,
) -> list[dict[str, Any]]:
    """Drive the middleware over a WebSocket scope; return the sent messages."""
    scope: dict[str, Any] = {
        "type": "websocket",
        "path": "/ws/pair",
        "headers": _encode(headers or {}),
    }
    if denial_extension:
        scope["extensions"] = {"websocket.http.response": {}}

    incoming = [{"type": "websocket.connect"}]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return incoming.pop(0) if incoming else {"type": "websocket.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


# ---------------------------------------------------------------------------
# HTTP: Origin allowlist (existing behaviour must be preserved)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_allowed_origin_passes():
    mw = CSRFOriginMiddleware(_downstream_http, allow_origins=[ALLOWED])
    assert await _run_http(mw, headers={"origin": ALLOWED}) == 200


@pytest.mark.asyncio
async def test_http_foreign_origin_rejected():
    mw = CSRFOriginMiddleware(_downstream_http, allow_origins=[ALLOWED])
    assert await _run_http(mw, headers={"origin": EVIL}) == 403


@pytest.mark.asyncio
async def test_http_safe_method_never_checked():
    mw = CSRFOriginMiddleware(_downstream_http, allow_origins=[ALLOWED])
    assert await _run_http(mw, method="GET", headers={"origin": EVIL}) == 200


@pytest.mark.asyncio
async def test_http_wildcard_allows_any_origin():
    mw = CSRFOriginMiddleware(_downstream_http, allow_origins=["*"])
    assert await _run_http(mw, headers={"origin": EVIL}) == 200


# ---------------------------------------------------------------------------
# HTTP: Sec-Fetch-Site fallback when Origin is absent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_no_origin_no_sec_fetch_passes():
    """curl / server-to-server SDKs send neither header and must keep working."""
    mw = CSRFOriginMiddleware(_downstream_http, allow_origins=[ALLOWED])
    assert await _run_http(mw, headers={}) == 200


@pytest.mark.asyncio
async def test_http_no_origin_but_cross_site_rejected():
    mw = CSRFOriginMiddleware(_downstream_http, allow_origins=[ALLOWED])
    assert await _run_http(mw, headers={"sec-fetch-site": "cross-site"}) == 403


@pytest.mark.asyncio
async def test_http_no_origin_cross_site_rejected_even_with_wildcard():
    """The wildcard used to make the whole guard a no-op — it no longer does."""
    mw = CSRFOriginMiddleware(_downstream_http, allow_origins=["*"])
    assert await _run_http(mw, headers={"sec-fetch-site": "cross-site"}) == 403


@pytest.mark.asyncio
async def test_http_cross_site_value_is_case_insensitive():
    mw = CSRFOriginMiddleware(_downstream_http, allow_origins=["*"])
    assert await _run_http(mw, headers={"sec-fetch-site": " Cross-Site "}) == 403


@pytest.mark.parametrize("site", ["same-origin", "same-site", "none"])
@pytest.mark.asyncio
async def test_http_non_cross_site_fetch_metadata_passes(site: str):
    mw = CSRFOriginMiddleware(_downstream_http, allow_origins=[ALLOWED])
    assert await _run_http(mw, headers={"sec-fetch-site": site}) == 200


@pytest.mark.asyncio
async def test_http_allowed_origin_wins_over_cross_site():
    """An allowlisted origin is legitimate cross-site traffic; do not reject it."""
    mw = CSRFOriginMiddleware(_downstream_http, allow_origins=[ALLOWED])
    headers = {"origin": ALLOWED, "sec-fetch-site": "cross-site"}
    assert await _run_http(mw, headers=headers) == 200


# ---------------------------------------------------------------------------
# WebSocket: CSWSH
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_allowed_origin_reaches_the_app():
    downstream = _WSRecorder()
    mw = CSRFOriginMiddleware(downstream, allow_origins=[ALLOWED])
    sent = await _run_ws(mw, headers={"origin": ALLOWED})
    assert downstream.reached is True
    assert sent[0]["type"] == "websocket.accept"


@pytest.mark.asyncio
async def test_ws_foreign_origin_denied_with_http_403():
    downstream = _WSRecorder()
    mw = CSRFOriginMiddleware(downstream, allow_origins=[ALLOWED])
    sent = await _run_ws(mw, headers={"origin": EVIL})

    assert downstream.reached is False
    assert sent[0]["type"] == "websocket.http.response.start"
    assert sent[0]["status"] == 403
    assert sent[1]["type"] == "websocket.http.response.body"
    assert b"origin not allowed" in sent[1]["body"]


@pytest.mark.asyncio
async def test_ws_foreign_origin_closes_when_denial_unsupported():
    """Servers without the denial extension get a pre-accept close, not a hang."""
    downstream = _WSRecorder()
    mw = CSRFOriginMiddleware(downstream, allow_origins=[ALLOWED])
    sent = await _run_ws(mw, headers={"origin": EVIL}, denial_extension=False)

    assert downstream.reached is False
    assert len(sent) == 1
    assert sent[0]["type"] == "websocket.close"
    assert sent[0]["code"] == 1008


@pytest.mark.asyncio
async def test_ws_handshake_is_always_answered():
    """Never a bare `return`: a denied handshake must produce ASGI output."""
    mw = CSRFOriginMiddleware(_WSRecorder(), allow_origins=[ALLOWED])
    for extension in (True, False):
        sent = await _run_ws(mw, headers={"origin": EVIL}, denial_extension=extension)
        assert sent, "denied handshake left the client hanging"


@pytest.mark.asyncio
async def test_ws_wildcard_allows_any_origin():
    downstream = _WSRecorder()
    mw = CSRFOriginMiddleware(downstream, allow_origins=["*"])
    await _run_ws(mw, headers={"origin": EVIL})
    assert downstream.reached is True


@pytest.mark.asyncio
async def test_ws_non_browser_client_without_origin_passes():
    downstream = _WSRecorder()
    mw = CSRFOriginMiddleware(downstream, allow_origins=[ALLOWED])
    await _run_ws(mw, headers={})
    assert downstream.reached is True


@pytest.mark.asyncio
async def test_ws_no_origin_but_cross_site_denied():
    downstream = _WSRecorder()
    mw = CSRFOriginMiddleware(downstream, allow_origins=["*"])
    sent = await _run_ws(mw, headers={"sec-fetch-site": "cross-site"})
    assert downstream.reached is False
    assert sent[0]["type"] == "websocket.http.response.start"


# ---------------------------------------------------------------------------
# End-to-end through Starlette, exercising the real handshake plumbing
# ---------------------------------------------------------------------------


def _build_app() -> Starlette:
    async def echo(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("connected")
        await websocket.close()

    app = Starlette(routes=[WebSocketRoute("/ws", echo)])
    app.add_middleware(CSRFOriginMiddleware, allow_origins=[ALLOWED])
    return app


def test_e2e_ws_foreign_origin_is_rejected():
    with TestClient(_build_app()) as client:
        with pytest.raises(WebSocketDenialResponse) as exc:
            with client.websocket_connect("/ws", headers={"origin": EVIL}):
                pass  # pragma: no cover - the handshake must never succeed
    assert exc.value.status_code == 403
    assert "origin not allowed" in json.loads(exc.value.content)["detail"]


def test_e2e_ws_allowed_origin_connects():
    with TestClient(_build_app()) as client:
        with client.websocket_connect("/ws", headers={"origin": ALLOWED}) as ws:
            assert ws.receive_text() == "connected"
