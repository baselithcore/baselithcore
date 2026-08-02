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
