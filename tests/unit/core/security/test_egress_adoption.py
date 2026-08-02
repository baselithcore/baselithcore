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


# ---------------------------------------------------------------------------
# A2A client
# ---------------------------------------------------------------------------


def _agent_client(**config_overrides):
    from core.a2a.agent_card import AgentCard
    from core.a2a.client import A2AClient, A2AClientConfig

    card = AgentCard(name="peer", description="d", endpoint="http://internal.example/agent")
    return A2AClient(card, config=A2AClientConfig(**config_overrides))


def test_a2a_client_refuses_internal_endpoint_when_strict(monkeypatch):
    _dns(monkeypatch, {"internal.example": ["10.0.0.5"]})
    client = _agent_client(allow_internal_endpoints=False)
    with pytest.raises(SsrfError):
        client._assert_endpoint_safe("http://internal.example/agent")


def test_a2a_client_allows_internal_endpoint_by_default(monkeypatch):
    # A2A meshes commonly run peer agents on internal networks (see
    # core/a2a/client.py:A2AClient.endpoint) - default posture must not
    # regress this, only the opt-out (allow_internal_endpoints=False) does.
    _dns(monkeypatch, {"internal.example": ["10.0.0.5"]})
    client = _agent_client()
    client._assert_endpoint_safe("http://internal.example/agent")  # does not raise


def test_a2a_client_refuses_non_http_scheme_even_with_internal_allowed():
    client = _agent_client()
    with pytest.raises(SsrfError):
        client._assert_endpoint_safe("file:///etc/passwd")


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
    import core.config as core_config
    from core.plugins.exporters import router as exporters_router

    _dns(monkeypatch, {"internal-marketplace.example": ["10.1.2.3"]})

    class _FakePluginConfig:
        OFFICIAL_MARKETPLACE_URL = "http://internal-marketplace.example"

    monkeypatch.setattr(core_config, "get_plugin_config", lambda: _FakePluginConfig())

    with pytest.raises(SsrfError):
        await exporters_router._exchange_github_for_jwt(github_token="gho_x")
