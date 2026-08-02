"""Unit tests for the shared SSRF-hardened httpx client wrapper.

Covers the `plugins/baselithbot/http.py::hardened_client` factory used by
every channel adapter, integration, and skill in baselithbot for outbound
HTTP: production-default blocking of internal targets, and the
``BASELITHBOT_ALLOW_INTERNAL_WEBHOOKS`` opt-out for trusted local setups.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from core.security.ssrf import SsrfError
from plugins.baselithbot.http import hardened_client


async def test_channel_post_to_internal_webhook_blocked(monkeypatch):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    monkeypatch.delenv("BASELITHBOT_ALLOW_INTERNAL_WEBHOOKS", raising=False)
    async with hardened_client(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        with pytest.raises(SsrfError):
            await client.post("https://hook.corp.internal/x", json={})


async def test_env_optout_allows_internal(monkeypatch):
    monkeypatch.setenv("BASELITHBOT_ALLOW_INTERNAL_WEBHOOKS", "true")
    async with hardened_client(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        resp = await client.post("http://192.168.1.5/hook", json={})
    assert resp.status_code == 200
