"""Il transport hardened valida e pinna ogni richiesta, redirect inclusi."""

from __future__ import annotations

import socket

import httpx
import pytest

from core.security.http import create_hardened_async_client
from core.security.ssrf import SsrfError, SsrfPolicy


def _dns(monkeypatch, mapping: dict[str, list[str]]) -> None:
    def fake(host, port, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in mapping[host]]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


class _Recorder:
    """MockTransport handler che registra le richieste ricevute."""

    def __init__(self, responses: dict[str, httpx.Response]):
        self.requests: list[httpx.Request] = []
        self._responses = responses

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responses.get(str(request.url.host), httpx.Response(200, text="ok"))


async def test_request_is_pinned_and_host_header_preserved(monkeypatch):
    _dns(monkeypatch, {"ok.example": ["93.184.216.34"]})
    recorder = _Recorder({})
    client = create_hardened_async_client(transport=httpx.MockTransport(recorder))
    resp = await client.get("https://ok.example/path")
    assert resp.status_code == 200
    sent = recorder.requests[0]
    assert sent.url.host == "93.184.216.34"          # connessione all'IP pinnato
    assert sent.headers["Host"] == "ok.example"       # Host header originale
    assert sent.extensions.get("sni_hostname") == "ok.example"
    await client.aclose()


async def test_internal_target_blocked(monkeypatch):
    _dns(monkeypatch, {"evil.example": ["169.254.169.254"]})
    client = create_hardened_async_client(transport=httpx.MockTransport(_Recorder({})))
    with pytest.raises(SsrfError):
        await client.get("https://evil.example/")
    await client.aclose()


async def test_redirect_hop_also_validated(monkeypatch):
    # primo hop pubblico, redirect verso host interno → bloccato
    _dns(monkeypatch, {"ok.example": ["93.184.216.34"], "internal.example": ["127.0.0.1"]})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["Host"] == "ok.example":
            return httpx.Response(302, headers={"Location": "https://internal.example/steal"})
        return httpx.Response(200)

    client = create_hardened_async_client(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )
    with pytest.raises(SsrfError):
        await client.get("https://ok.example/")
    await client.aclose()


async def test_allow_internal_policy_passthrough(monkeypatch):
    recorder = _Recorder({})
    client = create_hardened_async_client(
        policy=SsrfPolicy(allow_internal=True),
        transport=httpx.MockTransport(recorder),
    )
    resp = await client.get("http://127.0.0.1:9000/local")
    assert resp.status_code == 200
    assert recorder.requests[0].url.host == "127.0.0.1"  # nessun pinning
    await client.aclose()


# FINDING 1: mounts/proxy/proxies bypass guard
def test_mounts_kwarg_rejected():
    with pytest.raises(ValueError, match="mounts.*bypass"):
        create_hardened_async_client(mounts={"https://": httpx.HTTPTransport()})


def test_proxy_kwarg_rejected():
    with pytest.raises(ValueError, match="proxy.*bypass"):
        create_hardened_async_client(proxy="http://p:3128")


def test_proxies_kwarg_rejected():
    with pytest.raises(ValueError, match="proxies.*bypass"):
        create_hardened_async_client(proxies={"https://": "http://p:3128"})


# FINDING 2: relative redirects use original hostname, not pinned IP
async def test_relative_redirect_with_allowed_hosts(monkeypatch):
    """Relative Location: header should be joined against original hostname."""
    _dns(monkeypatch, {"ok.example": ["93.184.216.34"]})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            # First hop returns relative redirect
            return httpx.Response(302, headers={"Location": "/next"})
        # Second hop
        return httpx.Response(200, text="ok")

    client = create_hardened_async_client(
        policy=SsrfPolicy(allowed_hosts=frozenset({"ok.example"})),
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    resp = await client.get("https://ok.example/")
    assert resp.status_code == 200
    await client.aclose()


# FINDING 3: default keep-alive disabled
async def test_default_keepalive_disabled():
    """Verify max_keepalive_connections=0 on inner pool when limits not provided."""
    # Without transport kwarg, inner AsyncHTTPTransport should have max_keepalive_connections=0
    client = create_hardened_async_client()
    inner_transport = client._transport._inner
    assert isinstance(inner_transport, httpx.AsyncHTTPTransport)
    # Verify the pool's max_keepalive_connections is 0
    assert inner_transport._pool._max_keepalive_connections == 0
    await client.aclose()


async def test_keepalive_override():
    """Verify custom limits are passed to inner transport."""
    # With explicit limits, they should be used
    custom_limits = httpx.Limits(max_keepalive_connections=5)
    client = create_hardened_async_client(limits=custom_limits)
    inner_transport = client._transport._inner
    assert isinstance(inner_transport, httpx.AsyncHTTPTransport)
    # Verify the pool's max_keepalive_connections uses the override
    assert inner_transport._pool._max_keepalive_connections == 5
    await client.aclose()


# FINDING 4: context manager protocol forwarded
async def test_context_manager_protocol():
    """__aenter__/__aexit__ are forwarded to inner transport."""
    recorder = _Recorder({})
    async with create_hardened_async_client(
        transport=httpx.MockTransport(recorder)
    ) as client:
        # If __aenter__/__aexit__ are not implemented, this would fail
        assert client is not None
