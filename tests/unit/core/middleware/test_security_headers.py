"""
Tests for the SecurityHeadersMiddleware ASGI response headers / CSP policy.
"""

from unittest.mock import MagicMock

import pytest

from core.middleware.security import SecurityHeadersMiddleware


async def _run_security_headers_middleware(
    middleware: SecurityHeadersMiddleware,
    path: str = "/",
) -> dict[str, str]:
    """Drive the ASGI middleware end-to-end and return the merged header map."""

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware.app = downstream
    sent: list = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    await middleware(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return {k.decode(): v.decode() for k, v in start["headers"]}


@pytest.mark.asyncio
async def test_security_headers_middleware(mock_security_config):
    middleware = SecurityHeadersMiddleware(MagicMock(), config=mock_security_config)
    headers = await _run_security_headers_middleware(middleware)
    assert headers["x-frame-options"] == "DENY"
    assert headers["content-security-policy"] == "default-src 'self'"


@pytest.mark.asyncio
async def test_security_headers_middleware_sets_default_csp(mock_security_config):
    mock_security_config.content_security_policy = None
    middleware = SecurityHeadersMiddleware(MagicMock(), config=mock_security_config)
    headers = await _run_security_headers_middleware(middleware)
    assert "content-security-policy" in headers
    assert "default-src 'self'" in headers["content-security-policy"]


@pytest.mark.asyncio
async def test_docs_routes_get_relaxed_csp(mock_security_config):
    """Swagger UI / ReDoc pages must allow the jsDelivr CDN + inline bootstrap."""
    mock_security_config.content_security_policy = None
    middleware = SecurityHeadersMiddleware(MagicMock(), config=mock_security_config)
    for path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
        headers = await _run_security_headers_middleware(middleware, path=path)
        csp = headers["content-security-policy"]
        assert "https://cdn.jsdelivr.net" in csp
        assert "'unsafe-inline'" in csp.split("script-src", 1)[1].split(";", 1)[0]


@pytest.mark.asyncio
async def test_non_docs_routes_keep_strict_csp(mock_security_config):
    """Every non-docs route keeps the strict script-src 'self' policy."""
    mock_security_config.content_security_policy = None
    middleware = SecurityHeadersMiddleware(MagicMock(), config=mock_security_config)
    for path in ("/", "/console", "/chat", "/documentation"):
        csp = (await _run_security_headers_middleware(middleware, path=path))[
            "content-security-policy"
        ]
        assert "cdn.jsdelivr.net" not in csp
        assert "script-src 'self';" in csp


@pytest.mark.asyncio
async def test_operator_csp_override_wins_on_docs(mock_security_config):
    """An explicit operator CSP is never overridden, even on docs routes."""
    mock_security_config.content_security_policy = "default-src 'none'"
    middleware = SecurityHeadersMiddleware(MagicMock(), config=mock_security_config)
    headers = await _run_security_headers_middleware(middleware, path="/docs")
    assert headers["content-security-policy"] == "default-src 'none'"


@pytest.mark.asyncio
async def test_default_csp_has_no_bare_websocket_sources(mock_security_config):
    """A scheme-only ws:/wss: source matches EVERY host — an XSS foothold could
    exfiltrate over WebSocket despite the otherwise strict policy. 'self'
    already covers same-origin sockets in CSP3 browsers."""
    mock_security_config.content_security_policy = None
    middleware = SecurityHeadersMiddleware(MagicMock(), config=mock_security_config)
    csp = (await _run_security_headers_middleware(middleware))[
        "content-security-policy"
    ]
    connect_src = csp.split("connect-src", 1)[1].split(";", 1)[0]
    assert "ws:" not in connect_src
    assert "wss:" not in connect_src
    assert "'self'" in connect_src


@pytest.mark.asyncio
async def test_default_csp_allows_blob_images(mock_security_config):
    """A plugin SPA renders an API-fetched image through `URL.createObjectURL`,
    so `img-src` must accept `blob:` or the picture is blocked outright."""
    mock_security_config.content_security_policy = None
    middleware = SecurityHeadersMiddleware(MagicMock(), config=mock_security_config)
    csp = (await _run_security_headers_middleware(middleware))[
        "content-security-policy"
    ]
    img_src = csp.split("img-src", 1)[1].split(";", 1)[0]
    assert "blob:" in img_src
