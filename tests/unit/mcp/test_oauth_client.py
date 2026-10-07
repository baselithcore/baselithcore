"""Item 8: OAuth client library (RFC 9728, 8414, 7591, 8707, PKCE S256)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr

from core.mcp.client_errors import MCPClientError, MCPTransportError
from core.mcp.oauth_client import (
    MCPOAuthError,
    OAuthClient,
    generate_pkce,
)

from .fakes.oauth_server import ISSUER, ISSUER_WITH_PATH, RESOURCE, FakeOAuth

REDIRECT = "https://app.example.com/oauth/callback"


def _client(fake: FakeOAuth, **kw: Any) -> OAuthClient:
    return OAuthClient(httpx_transport=fake.transport(), **kw)


@pytest.fixture(autouse=True)
def _allow(internal_ok: None) -> None:
    return None


# --- RFC 9728 -------------------------------------------------------------


async def test_resource_metadata_from_the_challenge_url() -> None:
    fake = FakeOAuth()
    url = "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
    meta = await _client(fake).discover_protected_resource(
        RESOURCE, resource_metadata_url=url
    )
    assert meta.resource == RESOURCE
    assert meta.authorization_servers == (ISSUER,)
    assert meta.scopes_supported == ("read", "write")
    assert str(fake.requests[0].url) == url


async def test_resource_metadata_well_known_path_then_root() -> None:
    fake = FakeOAuth()
    await _client(fake).discover_protected_resource(RESOURCE)
    assert fake.requests[0].url.path == "/.well-known/oauth-protected-resource/mcp"

    fake2 = FakeOAuth()
    calls: list[str] = []
    inner = fake2._handle

    def routed(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/mcp"):
            return httpx.Response(404)
        return inner(request)

    c = OAuthClient(httpx_transport=httpx.MockTransport(routed))
    meta = await c.discover_protected_resource(RESOURCE)
    assert calls == [
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-protected-resource",
    ]
    assert meta.resource == RESOURCE


async def test_resource_mismatch_is_refused() -> None:
    fake = FakeOAuth()
    fake.pr_metadata = {
        "resource": "https://evil.example.com/mcp",
        "authorization_servers": [ISSUER],
    }
    with pytest.raises(MCPOAuthError, match="resource"):
        await _client(fake).discover_protected_resource(RESOURCE)


async def test_missing_metadata_is_a_typed_error() -> None:
    fake = FakeOAuth()
    fake.pr_metadata = None
    with pytest.raises(MCPOAuthError):
        await _client(fake).discover_protected_resource(RESOURCE)


# --- RFC 8414 -------------------------------------------------------------


async def test_authorization_server_metadata() -> None:
    fake = FakeOAuth()
    meta = await _client(fake).discover_authorization_server(ISSUER)
    assert meta.issuer == ISSUER
    assert meta.token_endpoint == f"{ISSUER}/token"
    assert meta.registration_endpoint == f"{ISSUER}/register"
    assert fake.requests[0].url.path == "/.well-known/oauth-authorization-server"


async def test_issuer_with_path_inserts_the_well_known_segment() -> None:
    fake = FakeOAuth()
    fake.as_metadata = FakeOAuth.default_as(ISSUER_WITH_PATH)
    meta = await _client(fake).discover_authorization_server(ISSUER_WITH_PATH)
    assert meta.issuer == ISSUER_WITH_PATH
    assert (
        fake.requests[0].url.path == "/.well-known/oauth-authorization-server/tenant1"
    )


async def test_falls_back_to_openid_configuration() -> None:
    fake = FakeOAuth()
    fake.available = {"openid"}
    meta = await _client(fake).discover_authorization_server(ISSUER)
    assert meta.issuer == ISSUER
    assert any("openid-configuration" in r.url.path for r in fake.requests)


async def test_issuer_mismatch_is_refused() -> None:
    fake = FakeOAuth()
    fake.as_metadata = FakeOAuth.default_as("https://other.example.com")
    with pytest.raises(MCPOAuthError, match="issuer"):
        await _client(fake).discover_authorization_server(ISSUER)


async def test_server_without_s256_is_refused() -> None:
    fake = FakeOAuth()
    fake.as_metadata["code_challenge_methods_supported"] = ["plain"]
    with pytest.raises(MCPOAuthError, match="S256"):
        await _client(fake).discover_authorization_server(ISSUER)
    fake.as_metadata.pop("code_challenge_methods_supported")
    with pytest.raises(MCPOAuthError, match="S256"):
        await _client(fake).discover_authorization_server(ISSUER)


async def test_non_https_endpoint_in_metadata_is_refused() -> None:
    fake = FakeOAuth()
    fake.as_metadata["token_endpoint"] = "http://auth.example.com/token"
    with pytest.raises(MCPOAuthError, match="https"):
        await _client(fake).discover_authorization_server(ISSUER)


# --- PKCE and the authorization URL ---------------------------------------


def test_pkce_s256() -> None:
    pair = generate_pkce()
    verifier = pair.verifier.get_secret_value()
    assert 43 <= len(verifier) <= 128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    assert pair.challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert pair.method == "S256"
    assert verifier not in repr(pair)
    assert generate_pkce().verifier.get_secret_value() != verifier


async def test_authorization_request_carries_resource_pkce_and_state() -> None:
    fake = FakeOAuth()
    c = _client(fake)
    server = await c.discover_authorization_server(ISSUER)
    req = c.build_authorization_request(
        server,
        client_id="cid",
        redirect_uri=REDIRECT,
        resource=RESOURCE,
        scopes=["read", "write"],
    )
    q = parse_qs(urlparse(req.url).query)
    assert req.url.startswith(f"{ISSUER}/authorize?")
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["cid"]
    assert q["redirect_uri"] == [REDIRECT]
    assert q["resource"] == [RESOURCE]
    assert q["scope"] == ["read write"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] == [req.state]
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(req.code_verifier.get_secret_value().encode()).digest()
    ).rstrip(b"=")
    assert q["code_challenge"] == [expected.decode()]
    assert req.code_verifier.get_secret_value() not in req.url


async def test_resource_with_fragment_is_rejected() -> None:
    fake = FakeOAuth()
    c = _client(fake)
    server = await c.discover_authorization_server(ISSUER)
    with pytest.raises(MCPOAuthError, match="resource"):
        c.build_authorization_request(
            server, client_id="c", redirect_uri=REDIRECT, resource=RESOURCE + "#frag"
        )


# --- RFC 7591 --------------------------------------------------------------


async def test_dynamic_client_registration() -> None:
    fake = FakeOAuth()
    c = _client(fake)
    server = await c.discover_authorization_server(ISSUER)
    reg = await c.register_client(server, client_name="Lith", redirect_uris=[REDIRECT])
    assert isinstance(reg.client_id, SecretStr)
    assert reg.client_id.get_secret_value() == "dyn-client-1"
    assert reg.client_secret is not None
    assert reg.client_secret.get_secret_value() == "dyn-secret-1"
    assert "dyn-secret-1" not in repr(reg)
    assert fake.json_body["redirect_uris"] == [REDIRECT]
    assert fake.json_body["client_name"] == "Lith"
    assert fake.json_body["token_endpoint_auth_method"] == "none"


async def test_registration_without_endpoint_is_a_typed_error() -> None:
    fake = FakeOAuth()
    fake.as_metadata.pop("registration_endpoint")
    c = _client(fake)
    server = await c.discover_authorization_server(ISSUER)
    with pytest.raises(MCPOAuthError, match="registration"):
        await c.register_client(server, client_name="x", redirect_uris=[REDIRECT])


# --- token endpoint --------------------------------------------------------


async def test_code_exchange_sends_pkce_verifier_and_resource() -> None:
    fake = FakeOAuth()
    c = _client(fake)
    server = await c.discover_authorization_server(ISSUER)
    before = time.time()
    tokens = await c.exchange_code(
        server,
        client_id="cid",
        redirect_uri=REDIRECT,
        code="the-code",
        code_verifier=SecretStr("v" * 50),
        resource=RESOURCE,
    )
    assert fake.form["grant_type"] == ["authorization_code"]
    assert fake.form["code"] == ["the-code"]
    assert fake.form["code_verifier"] == ["v" * 50]
    assert fake.form["resource"] == [RESOURCE]
    assert fake.form["client_id"] == ["cid"]
    assert tokens.access_token.get_secret_value() == "at-123"
    assert tokens.refresh_token is not None
    assert tokens.refresh_token.get_secret_value() == "rt-456"
    assert tokens.expires_at is not None and tokens.expires_at >= before + 3500
    assert "at-123" not in repr(tokens)


async def test_refresh_sends_resource_and_secret() -> None:
    fake = FakeOAuth()
    c = _client(fake)
    server = await c.discover_authorization_server(ISSUER)
    await c.refresh(
        server,
        client_id="cid",
        client_secret=SecretStr("shh"),
        refresh_token=SecretStr("rt-456"),
        resource=RESOURCE,
    )
    assert fake.form["grant_type"] == ["refresh_token"]
    assert fake.form["refresh_token"] == ["rt-456"]
    assert fake.form["resource"] == [RESOURCE]
    assert fake.form["client_secret"] == ["shh"]


async def test_token_error_response_is_typed_and_has_no_secrets() -> None:
    fake = FakeOAuth()
    fake.token_status = 400
    fake.token_response = {
        "error": "invalid_grant",
        "error_description": "bad sk-live-ABCDEFGHIJKLMNOP123",
    }
    c = _client(fake)
    server = await c.discover_authorization_server(ISSUER)
    with pytest.raises(MCPOAuthError) as info:
        await c.refresh(
            server, client_id="c", refresh_token=SecretStr("r"), resource=RESOURCE
        )
    assert info.value.error == "invalid_grant"
    assert "sk-live-ABCDEFGHIJKLMNOP123" not in str(info.value)
    assert isinstance(info.value, MCPClientError)


async def test_malformed_token_response_is_refused() -> None:
    fake = FakeOAuth()
    fake.token_response = {"token_type": "Bearer"}
    c = _client(fake)
    server = await c.discover_authorization_server(ISSUER)
    with pytest.raises(MCPOAuthError, match="access_token"):
        await c.refresh(
            server, client_id="c", refresh_token=SecretStr("r"), resource=RESOURCE
        )


# --- bounds ----------------------------------------------------------------


async def test_oversized_metadata_is_cut_off() -> None:
    def big(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{" + b" " * (2 << 20) + b"}")

    c = OAuthClient(
        httpx_transport=httpx.MockTransport(big), max_response_bytes=64 * 1024
    )
    with pytest.raises(MCPTransportError, match="too large"):
        await c.discover_authorization_server(ISSUER)


async def test_slow_server_is_bounded_by_the_timeout() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200, json={})

    c = OAuthClient(httpx_transport=httpx.MockTransport(slow), timeout=0.3)
    started = time.monotonic()
    with pytest.raises(MCPTransportError):
        await c.discover_authorization_server(ISSUER)
    assert time.monotonic() - started < 3.0


async def test_redirects_are_not_followed() -> None:
    seen: list[str] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://169.254.169.254/x"})

    c = OAuthClient(httpx_transport=httpx.MockTransport(redirect))
    with pytest.raises(MCPClientError):
        await c.discover_authorization_server(ISSUER)
    assert len(seen) == 1


async def test_ssrf_guard_blocks_internal_issuers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.config.mcp as cfg

    monkeypatch.setenv("MCP_ALLOW_INTERNAL_ENDPOINTS", "false")
    monkeypatch.setattr(cfg, "_mcp_config", None)
    c = OAuthClient()
    for target in ("https://127.0.0.1", "https://169.254.169.254", "https://localhost"):
        with pytest.raises(MCPClientError, match="(?i)blocked|ssrf|not allowed"):
            await c.discover_authorization_server(target)
    await c.aclose()


# --- revocation (RFC 7009) -------------------------------------------------


async def test_revoke_token_posts_the_token_to_the_revocation_endpoint() -> None:
    fake = FakeOAuth()
    fake.as_metadata["revocation_endpoint"] = f"{ISSUER}/revoke"
    seen: dict[str, Any] = {}
    inner = fake._handle

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/revoke"):
            seen["form"] = parse_qs(request.content.decode())
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200)
        return inner(request)

    c = OAuthClient(httpx_transport=httpx.MockTransport(handle))
    server = await c.discover_authorization_server(ISSUER)
    assert (
        await c.revoke_token(server, token=SecretStr("rt-1"), client_id="cid") is True
    )
    assert seen["form"]["token"] == ["rt-1"]
    assert seen["form"]["client_id"] == ["cid"] and seen["auth"] is None
    assert await c.revoke_token(
        server, token=SecretStr("rt-1"), client_id="cid", client_secret=SecretStr("s")
    )
    assert seen["auth"].startswith("Basic ") and "client_id" not in seen["form"]


async def test_revoke_token_without_an_endpoint_does_nothing() -> None:
    fake = FakeOAuth()
    c = _client(fake)
    server = await c.discover_authorization_server(ISSUER)
    assert server.revocation_endpoint is None
    assert await c.revoke_token(server, token="t", client_id="c") is False
