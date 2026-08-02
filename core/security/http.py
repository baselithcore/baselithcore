"""SSRF-hardened httpx client factory.

``create_hardened_async_client`` wraps the (real or mock) transport in
:class:`SsrfBlockingTransport`, which re-validates and IP-pins every request
that reaches the wire — including every redirect hop that httpx follows —
so no call site can forget the guard.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from core.security.ssrf import SsrfPolicy, resolve_pinned_target

_DEFAULT_POLICY = SsrfPolicy()


class SsrfBlockingTransport(httpx.AsyncBaseTransport):
    """Transport wrapper: validate + pin each request before delegating."""

    def __init__(self, inner: httpx.AsyncBaseTransport, policy: SsrfPolicy) -> None:
        self._inner = inner
        self._policy = policy

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        pinned_url, host = await asyncio.to_thread(
            resolve_pinned_target, str(request.url), self._policy
        )
        if pinned_url != str(request.url):
            request.url = httpx.URL(pinned_url)
            request.headers["Host"] = host
            request.extensions["sni_hostname"] = host
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def create_hardened_async_client(
    policy: SsrfPolicy | None = None, **httpx_kwargs: Any
) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient`` whose every request passes the SSRF guard.

    Args:
        policy: Egress policy; defaults to the strict :class:`SsrfPolicy`.
        **httpx_kwargs: Forwarded to ``httpx.AsyncClient``. A ``transport``
            kwarg, if given (e.g. a ``MockTransport`` in tests), is wrapped
            rather than replaced.
    """
    inner = httpx_kwargs.pop("transport", None) or httpx.AsyncHTTPTransport()
    return httpx.AsyncClient(
        transport=SsrfBlockingTransport(inner, policy or _DEFAULT_POLICY),
        **httpx_kwargs,
    )
