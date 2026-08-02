"""I call site core rifiutano endpoint interni (SSRF guard adottata).

Copre Task 4 del piano "Fase 1 - SSRF unificato": OIDC discovery, A2A client,
MCP HTTP transport, finetuning providers, exporters router.
"""

from __future__ import annotations

import socket

import pytest

from core.security.ssrf import SsrfError


def _dns(monkeypatch, mapping):
    def fake(host, port, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in mapping[host]]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


# ---------------------------------------------------------------------------
# OIDC discovery
# ---------------------------------------------------------------------------


def test_oidc_discovery_refuses_internal_issuer(monkeypatch):
    from core.auth import oidc

    _dns(monkeypatch, {"idp.internal": ["192.168.0.10"]})
    with pytest.raises(SsrfError):
        oidc._assert_issuer_safe("https://idp.internal")


def test_oidc_discovery_allows_public_issuer(monkeypatch):
    from core.auth import oidc

    _dns(monkeypatch, {"idp.example.com": ["93.184.216.34"]})
    oidc._assert_issuer_safe("https://idp.example.com")  # does not raise


class _FakeHttpxResponse:
    """Minimal stand-in for httpx.Response used by the pinned-client stubs."""

    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _recording_httpx_client(monkeypatch, responses):
    """Patch ``httpx.Client`` (as used by ``oidc._pinned_get``) to record every
    ``.get(url, headers=..., extensions=..., timeout=...)`` call and return
    ``responses`` in order.

    Returns the list of recorded calls (populated as the code under test runs).
    """
    import httpx as httpx_module

    calls: list[dict] = []

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, *, headers=None, extensions=None, timeout=None):
            calls.append({"url": url, "headers": headers, "extensions": extensions})
            return _FakeHttpxResponse(responses[len(calls) - 1])

    monkeypatch.setattr(httpx_module, "Client", _FakeClient)
    return calls


def _rsa_jwk(kid: str):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update({"kid": kid, "use": "sig"})
    return key, jwk


def _oidc_config(**overrides):
    from core.config.security import SecurityConfig

    base = dict(
        SECRET_KEY="x" * 40,
        OIDC_ENABLED=True,
        OIDC_ISSUER="https://idp.example.com",
        OIDC_AUDIENCE="baselith-api",
    )
    base.update(overrides)
    return SecurityConfig(**base)


def test_oidc_discovery_fetch_uses_pinned_path(monkeypatch):
    """Finding 3: the discovery GET must not re-resolve DNS unpinned after
    ``_assert_issuer_safe`` validates the issuer once."""
    from core.auth import oidc

    _dns(monkeypatch, {"idp.example.com": ["93.184.216.34"]})
    calls = _recording_httpx_client(
        monkeypatch, [{"jwks_uri": "https://idp.example.com/jwks"}]
    )

    verifier = oidc.OIDCVerifier(config=_oidc_config())
    jwks_uri = verifier._jwks_uri()

    assert jwks_uri == "https://idp.example.com/jwks"
    assert len(calls) == 1
    assert calls[0]["headers"]["Host"] == "idp.example.com"
    assert calls[0]["extensions"] == {"sni_hostname": "idp.example.com"}
    # The request target is the pinned (resolved-IP) URL, not the hostname -
    # proves resolve_pinned_target's output is what actually got requested.
    assert "idp.example.com" not in calls[0]["url"]
    assert "93.184.216.34" in calls[0]["url"]


def test_oidc_jwks_fetch_uses_pinned_path(monkeypatch):
    """Finding 1: the JWKS document fetch must go through the same pinned
    path as discovery - never PyJWKClient's unguarded ``urlopen``."""
    from core.auth import oidc

    _dns(monkeypatch, {"idp.example.com": ["93.184.216.34"]})
    _key, jwk = _rsa_jwk("kid-1")
    calls = _recording_httpx_client(monkeypatch, [{"keys": [jwk]}])

    verifier = oidc.OIDCVerifier(
        config=_oidc_config(OIDC_JWKS_URL="https://idp.example.com/jwks")
    )
    jwk_set = verifier._fetch_jwk_set()

    assert [k.key_id for k in jwk_set.keys] == ["kid-1"]
    assert len(calls) == 1
    assert calls[0]["headers"]["Host"] == "idp.example.com"
    assert "idp.example.com" not in calls[0]["url"]


def test_oidc_jwks_fetch_rejects_dns_rebind_to_internal(monkeypatch):
    """Finding 1: a JWKS host whose DNS resolves internal (compromised IdP
    DNS, or a rebind) is rejected before any HTTP call is made."""
    from core.auth import oidc

    _dns(monkeypatch, {"idp.example.com": ["10.0.0.9"]})
    verifier = oidc.OIDCVerifier(
        config=_oidc_config(OIDC_JWKS_URL="https://idp.example.com/jwks")
    )

    with pytest.raises(SsrfError):
        verifier._fetch_jwk_set()


def test_oidc_jwks_refetch_on_unknown_kid_uses_pinned_path(monkeypatch):
    """Finding 1: an unknown kid (key rotation) triggers exactly one
    re-fetch, and that re-fetch also goes through the pinned path."""
    import jwt as pyjwt

    from core.auth import oidc

    _dns(monkeypatch, {"idp.example.com": ["93.184.216.34"]})
    old_key, old_jwk = _rsa_jwk("old-kid")
    new_key, new_jwk = _rsa_jwk("new-kid")
    calls = _recording_httpx_client(
        monkeypatch, [{"keys": [old_jwk]}, {"keys": [new_jwk]}]
    )

    verifier = oidc.OIDCVerifier(
        config=_oidc_config(OIDC_JWKS_URL="https://idp.example.com/jwks")
    )
    # Prime the cache with the stale JWKS (simulates a verifier that has been
    # running since before the IdP rotated its signing key).
    verifier._fetch_jwk_set()
    assert len(calls) == 1

    token = pyjwt.encode(
        {"sub": "u"}, new_key, algorithm="RS256", headers={"kid": "new-kid"}
    )
    signing_key = verifier._resolve_signing_key(token)

    assert len(calls) == 2  # initial (cached) fetch + one pinned refresh
    for call in calls:
        assert call["headers"]["Host"] == "idp.example.com"
        assert "idp.example.com" not in call["url"]
    assert (
        signing_key.public_numbers() == new_key.public_key().public_numbers()
    )


# ---------------------------------------------------------------------------
# A2A client
# ---------------------------------------------------------------------------


def _agent_client(**config_overrides):
    from core.a2a.agent_card import AgentCard
    from core.a2a.client import A2AClient, A2AClientConfig

    card = AgentCard(name="peer", description="d", endpoint="http://internal.example/agent")
    return A2AClient(card, config=A2AClientConfig(**config_overrides))


async def test_a2a_client_refuses_internal_endpoint_when_strict(monkeypatch):
    _dns(monkeypatch, {"internal.example": ["10.0.0.5"]})
    client = _agent_client(allow_internal_endpoints=False)
    with pytest.raises(SsrfError):
        await client._assert_endpoint_safe("http://internal.example/agent")


async def test_a2a_client_allows_internal_endpoint_by_default(monkeypatch):
    # A2A meshes commonly run peer agents on internal networks (see
    # core/a2a/client.py:A2AClient.endpoint) - default posture must not
    # regress this, only the opt-out (allow_internal_endpoints=False) does.
    _dns(monkeypatch, {"internal.example": ["10.0.0.5"]})
    client = _agent_client()
    await client._assert_endpoint_safe("http://internal.example/agent")  # no raise


async def test_a2a_client_refuses_non_http_scheme_even_with_internal_allowed():
    client = _agent_client()
    with pytest.raises(SsrfError):
        await client._assert_endpoint_safe("file:///etc/passwd")


async def test_a2a_client_assert_endpoint_safe_offloads_dns(monkeypatch):
    """`_assert_endpoint_safe` must offload blocking DNS resolution rather
    than calling socket.getaddrinfo synchronously on the event loop thread
    (Finding 2)."""
    import threading

    resolving_thread: threading.Thread | None = None

    def fake_getaddrinfo(host, port, *args, **kwargs):
        nonlocal resolving_thread
        resolving_thread = threading.current_thread()
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    # allow_internal=False so the check actually resolves DNS (allow_internal
    # short-circuits before any getaddrinfo call - nothing to offload then).
    client = _agent_client(allow_internal_endpoints=False)
    await client._assert_endpoint_safe("http://internal.example/agent")

    assert resolving_thread is not None
    assert resolving_thread is not threading.main_thread()


def test_a2a_default_allow_internal_endpoints_reads_env(monkeypatch):
    from core.a2a.client import A2AClientConfig

    monkeypatch.delenv("A2A_ALLOW_INTERNAL_ENDPOINTS", raising=False)
    assert A2AClientConfig().allow_internal_endpoints is True

    monkeypatch.setenv("A2A_ALLOW_INTERNAL_ENDPOINTS", "false")
    assert A2AClientConfig().allow_internal_endpoints is False

    monkeypatch.setenv("A2A_ALLOW_INTERNAL_ENDPOINTS", "true")
    assert A2AClientConfig().allow_internal_endpoints is True


# ---------------------------------------------------------------------------
# MCP HTTP client transport
# ---------------------------------------------------------------------------


async def test_mcp_http_transport_refuses_internal_endpoint(monkeypatch):
    from core.mcp.http_client_transport import HTTPClientTransport

    _dns(monkeypatch, {"mcp-internal.example": ["172.16.0.9"]})
    transport = HTTPClientTransport("http://mcp-internal.example/mcp")
    with pytest.raises(SsrfError):
        await transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})


async def test_mcp_http_transport_allows_internal_when_configured(monkeypatch):
    import httpx

    from core.config import get_mcp_config
    from core.mcp.http_client_transport import HTTPClientTransport

    get_mcp_config.cache_clear() if hasattr(get_mcp_config, "cache_clear") else None
    monkeypatch.setenv("MCP_ALLOW_INTERNAL_ENDPOINTS", "true")
    import core.config.mcp as mcp_config_module

    monkeypatch.setattr(mcp_config_module, "_mcp_config", None)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    mock_transport = httpx.MockTransport(handler)
    transport = HTTPClientTransport(
        "http://mcp-internal.example/mcp", httpx_transport=mock_transport
    )
    try:
        result = await transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert result == {"jsonrpc": "2.0", "id": 1, "result": {}}
    finally:
        await transport.close()
        monkeypatch.setattr(mcp_config_module, "_mcp_config", None)


# ---------------------------------------------------------------------------
# Finetuning providers (together.ai) - hardcoded public SaaS endpoints, the
# guard is defense-in-depth: it must not reject the real, public hostname.
# ---------------------------------------------------------------------------


async def test_together_provider_uses_hardened_client(monkeypatch):
    from core.finetuning.providers import TogetherProvider

    provider = TogetherProvider(api_key="test-key")

    _dns(monkeypatch, {"api.together.xyz": ["93.184.216.34"]})

    import httpx

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    mock_transport = httpx.MockTransport(handler)

    import core.finetuning.providers as providers_module
    from core.security.http import create_hardened_async_client

    def _patched(*args, **kwargs):
        kwargs["transport"] = mock_transport
        return create_hardened_async_client(*args, **kwargs)

    monkeypatch.setattr(providers_module, "create_hardened_async_client", _patched)

    jobs = await provider.list_jobs()
    assert jobs == []


# ---------------------------------------------------------------------------
# Exporters router: GitHub -> marketplace JWT exchange
# ---------------------------------------------------------------------------


async def test_exporters_github_exchange_refuses_internal_marketplace_url(monkeypatch):
    from fastapi import HTTPException

    import core.config as core_config
    from core.plugins.exporters import router as exporters_router

    _dns(monkeypatch, {"internal-marketplace.example": ["10.1.2.3"]})

    class _FakePluginConfig:
        OFFICIAL_MARKETPLACE_URL = "http://internal-marketplace.example"

    monkeypatch.setattr(core_config, "get_plugin_config", lambda: _FakePluginConfig())

    # SsrfError is caught alongside httpx.HTTPError and degrades to a 502
    # (bad gateway), not an unhandled 500 — same pattern as
    # plugins/baselithbot/dashboard/security.py's probe_provider().
    with pytest.raises(HTTPException) as exc_info:
        await exporters_router._exchange_github_for_jwt(github_token="gho_x")
    assert exc_info.value.status_code == 502
