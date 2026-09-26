"""Same-origin browser requests pass the CSRF guard without an allowlist entry.

A page served by the deployment itself (the ``/admin`` dashboard) sends
``Origin: <own origin>`` and ``Sec-Fetch-Site: same-origin`` on every POST.
With the default empty ``ALLOW_ORIGINS`` those requests used to be rejected
with 403, although they cannot be a cross-site forgery.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.middleware.csrf import CSRFOriginMiddleware

EVIL = "https://evil.example.com"


async def _downstream(scope, receive, send) -> None:
    if scope["type"] == "websocket":
        await receive()
        await send({"type": "websocket.accept"})
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok", "more_body": False})


async def _status(
    headers: dict[str, str],
    *,
    scheme: str = "https",
    scope_type: str = "http",
    allow_origins: list[str] | None = None,
) -> int:
    """Drive the middleware; return the HTTP status (WS accept counts as 200)."""
    mw = CSRFOriginMiddleware(_downstream, allow_origins=allow_origins or [])
    scope: dict[str, Any] = {
        "type": scope_type,
        "scheme": scheme,
        "path": "/admin/reindex",
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        "extensions": {"websocket.http.response": {}},
    }
    if scope_type == "http":
        scope["method"] = "POST"
    incoming = [{"type": "websocket.connect"}]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        if scope_type == "http":
            return {"type": "http.request", "body": b"", "more_body": False}
        return incoming.pop(0) if incoming else {"type": "websocket.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await mw(scope, receive, send)
    for message in sent:
        if message["type"] == "websocket.accept":
            return 200
        if message["type"] in {"http.response.start", "websocket.http.response.start"}:
            return int(message["status"])
    raise AssertionError(f"no response sent: {sent}")


@pytest.mark.asyncio
async def test_same_origin_fetch_metadata_passes_with_empty_allowlist() -> None:
    headers = {
        "host": "api.example.com",
        "origin": "https://api.example.com",
        "sec-fetch-site": "same-origin",
    }
    assert await _status(headers) == 200


@pytest.mark.asyncio
async def test_origin_equal_to_request_origin_passes_without_fetch_metadata() -> None:
    """User agents that predate Fetch metadata still send a matching Origin."""
    headers = {"host": "api.example.com", "origin": "https://api.example.com"}
    assert await _status(headers) == 200


@pytest.mark.parametrize(
    ("host", "origin", "scheme"),
    [
        ("api.example.com:443", "https://api.example.com", "https"),
        ("API.example.com", "https://api.example.com:443", "https"),
        ("localhost:8000", "http://localhost:8000", "http"),
        ("[::1]:8000", "http://[::1]:8000", "http"),
    ],
)
@pytest.mark.asyncio
async def test_origin_match_normalises_default_ports_and_case(
    host: str, origin: str, scheme: str
) -> None:
    assert await _status({"host": host, "origin": origin}, scheme=scheme) == 200


@pytest.mark.parametrize(
    ("host", "origin", "scheme"),
    [
        # Different host: a genuine cross-site forgery.
        ("api.example.com", EVIL, "https"),
        # Scheme downgrade: http page posting to the https endpoint.
        ("api.example.com", "http://api.example.com", "https"),
        # Different port on the same host is a different origin.
        ("localhost:8000", "http://localhost:3000", "http"),
        # Sibling subdomain (same-site, not same-origin) needs an allowlist entry.
        ("api.example.com", "https://app.example.com", "https"),
        # Opaque origin from a sandboxed iframe / file: URL.
        ("api.example.com", "null", "https"),
    ],
)
@pytest.mark.asyncio
async def test_non_matching_origin_still_rejected(
    host: str, origin: str, scheme: str
) -> None:
    assert await _status({"host": host, "origin": origin}, scheme=scheme) == 403


@pytest.mark.asyncio
async def test_same_site_with_foreign_origin_is_rejected() -> None:
    """``same-site`` covers sibling hosts the operator may not control."""
    headers = {
        "host": "api.example.com",
        "origin": "https://user-content.example.com",
        "sec-fetch-site": "same-site",
    }
    assert await _status(headers) == 403


@pytest.mark.asyncio
async def test_cross_site_foreign_origin_is_rejected() -> None:
    """The browser-sent Origin of a cross-site page never equals the Host."""
    headers = {
        "host": "api.example.com",
        "origin": EVIL,
        "sec-fetch-site": "cross-site",
    }
    assert await _status(headers) == 403


@pytest.mark.asyncio
async def test_missing_host_header_does_not_match() -> None:
    assert await _status({"origin": "https://api.example.com"}) == 403


@pytest.mark.asyncio
async def test_websocket_same_origin_handshake_passes() -> None:
    headers = {"host": "api.example.com", "origin": "https://api.example.com"}
    assert await _status(headers, scheme="wss", scope_type="websocket") == 200


@pytest.mark.asyncio
async def test_websocket_foreign_origin_still_denied() -> None:
    headers = {"host": "api.example.com", "origin": EVIL}
    assert await _status(headers, scheme="wss", scope_type="websocket") == 403
